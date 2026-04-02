"""
ftui - dual-pane TUI file transfer client
Backend: prompt_toolkit (FormattedText tuple API) + rich progress
Protocols: FTP, FTPS, SFTP, SCP
"""
from __future__ import annotations

import os
import posixpath
import shutil
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from prompt_toolkit import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText, to_formatted_text
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import (
    ConditionalContainer,
    FormattedTextControl,
    HSplit,
    Layout,
    VSplit,
    Window,
)
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea

from ftui.protocols import FileEntry, connect
from ftui import bookmarks as bm

socket.setdefaulttimeout(15)

# ─────────────────────────────────────────────
# Palette
# ─────────────────────────────────────────────
STYLE = Style.from_dict({
    "header":       "bg:#161b22 #6ea8fe bold",
    "pane.title":   "bg:#161b22 #e6edf3 bold",
    "pane.title.f": "bg:#1f6feb #ffffff bold",
    "pane.path":    "bg:#0d1117 #388bfd",
    "pane.hdr":     "bg:#161b22 #6e7681 bold",
    "pane.dir":     "bg:#0d1117 #6ea8fe",
    "pane.dir.alt": "bg:#0c1016 #6ea8fe",
    "pane.file":    "bg:#0d1117 #c9d1d9",
    "pane.file.alt":"bg:#0c1016 #c9d1d9",
    "pane.cursor":  "bg:#1f6feb #ffffff bold",
    "pane.empty":   "bg:#0d1117 #0d1117",
    "sep":          "bg:#161b22 #30363d",
    "xfer":         "bg:#161b22 #388bfd",
    "xfer.bar":     "bg:#1f6feb #ffffff",
    "xfer.fill":    "bg:#161b22 #30363d",
    "status":       "bg:#161b22 #6e7681",
    "status.err":   "bg:#161b22 #f85149",
    "status.ok":    "bg:#161b22 #3fb950",
    "modal.bg":     "bg:#161b22 #c9d1d9",
    "modal.title":  "bg:#161b22 #6ea8fe bold",
    "modal.label":  "bg:#161b22 #6e7681",
    "modal.active": "bg:#0d1117 #ffffff",
    "modal.key":    "bg:#161b22 #388bfd bold",
})

FT = FormattedText  # alias


def _safe(s: str) -> str:
    """Strip control chars that could break rendering."""
    return "".join(c if c >= " " else "?" for c in s)


# ─────────────────────────────────────────────
# Pane state
# ─────────────────────────────────────────────
class PaneState:
    def __init__(self, is_local: bool):
        self.is_local  = is_local
        self.path      = str(Path.home()) if is_local else "/"
        self.entries: list[FileEntry] = []
        self.cursor    = 0
        self.offset    = 0
        self.client    = None
        self.loading   = False
        self.connected = False
        self.error     = ""

    def _all(self) -> list[FileEntry]:
        at_root = self.path in ("/", "")
        pre = [] if at_root else [FileEntry(name="..", size=0, is_dir=True)]
        return pre + self.entries

    def selected(self) -> Optional[FileEntry]:
        all_ = self._all()
        return all_[self.cursor] if 0 <= self.cursor < len(all_) else None

    def move(self, delta: int, vis_h: int):
        total = len(self._all())
        if total == 0:
            return
        self.cursor = max(0, min(total - 1, self.cursor + delta))
        if self.cursor < self.offset:
            self.offset = self.cursor
        elif self.cursor >= self.offset + vis_h:
            self.offset = self.cursor - vis_h + 1

    def enter_path(self) -> Optional[str]:
        e = self.selected()
        if not e or not e.is_dir:
            return None
        if e.name == "..":
            if self.is_local:
                return str(Path(self.path).parent)
            return posixpath.dirname(self.path.rstrip("/")) or "/"
        if self.is_local:
            return os.path.join(self.path, e.name)
        return posixpath.join(self.path, e.name)


# ─────────────────────────────────────────────
# Transfer state
# ─────────────────────────────────────────────
class XferState:
    def __init__(self):
        self.reset()

    def reset(self):
        self.active      = False
        self.name        = ""
        self.direction   = ""
        self.transferred = 0
        self.total       = 0
        self.done_files  = 0
        self.total_files = 0
        self.is_dir      = False

    @property
    def pct(self) -> int:
        return min(100, int(self.transferred / self.total * 100)) if self.total > 0 else 0

    def bar_ft(self, width: int) -> FormattedText:
        filled = int(width * self.pct / 100)
        empty  = width - filled
        return FT([
            ("class:xfer.bar",  "█" * filled),
            ("class:xfer.fill", "░" * empty),
        ])


# ─────────────────────────────────────────────
# Renderer helpers
# ─────────────────────────────────────────────
def _pad(s: str, w: int) -> str:
    if len(s) > w:
        return s[:w - 1] + "~"
    return s.ljust(w)


def _rpad(s: str, w: int) -> str:
    return s.rjust(w)


def render_pane(state: PaneState, focused: bool, width: int, height: int) -> FormattedText:
    """
    Render one file pane as a list of (style, text) tuples.
    No HTML parsing — zero chance of XML crash.
    """
    frags = []
    W = width  # total width including borders

    # ── Title ──
    if state.is_local:
        title = "Local"
    elif state.connected:
        proto = state.client.protocol if state.client else ""
        title = f"Remote  [{proto}]"
    else:
        title = "Remote  [not connected]"
    if state.loading:
        title += "  ..."
    t_style = "class:pane.title.f" if focused else "class:pane.title"
    frags.append((t_style, _pad(f" {title}", W) + "\n"))

    # ── Path ──
    path_disp = _safe(state.path)
    if len(path_disp) > W - 2:
        path_disp = "..." + path_disp[-(W - 5):]
    frags.append(("class:pane.path", _pad(f" {path_disp}", W) + "\n"))

    # ── Column header ──
    name_w = W - 11
    frags.append(("class:pane.hdr", _pad(f" {'Name'}", name_w + 1) + _rpad("Size ", 10) + "\n"))

    # ── Entries ──
    all_entries = state._all()
    vis_h = height - 3  # title + path + header
    visible = all_entries[state.offset: state.offset + vis_h]

    for i, e in enumerate(visible):
        abs_i = state.offset + i
        is_cur = abs_i == state.cursor
        alt = abs_i % 2 == 1

        if is_cur:
            style = "class:pane.cursor"
        elif e.is_dir:
            style = "class:pane.dir.alt" if alt else "class:pane.dir"
        else:
            style = "class:pane.file.alt" if alt else "class:pane.file"

        marker  = ">" if is_cur else " "
        prefix  = "DIR " if e.is_dir else "    "
        name    = _safe(e.name)
        name_str = _pad(f"{marker}{prefix}{name}", name_w + 1)
        size_str = _rpad(e.size_human, 9)
        frags.append((style, name_str + size_str + "\n"))

    # ── Empty rows ──
    empty_rows = vis_h - len(visible)
    for _ in range(empty_rows):
        frags.append(("class:pane.empty", " " * W + "\n"))

    return FT(frags)


def render_transfer(xfer: XferState, width: int) -> FormattedText:
    frags = []
    bar_w = max(10, width - 50)

    if xfer.is_dir:
        label = f" {xfer.direction}  {_safe(xfer.name)}/  [{xfer.done_files}/{xfer.total_files} files]  "
    else:
        done_kb  = xfer.transferred / 1024
        total_kb = xfer.total / 1024
        label = f" {xfer.direction}  {_safe(xfer.name)}  {done_kb:.0f}/{total_kb:.0f} KB  "

    frags.append(("class:xfer", label))
    frags.extend(xfer.bar_ft(bar_w))
    frags.append(("class:xfer", f"  {xfer.pct}%\n"))
    return FT(frags)


def render_status(msg: str, is_error: bool, width: int) -> FormattedText:
    keys = " F2 Connect  F3 Bookmarks  F5 Transfer  F7 Mkdir  F8 Delete  F9 Rename  Tab Switch  Q Quit"
    text  = f" {_safe(msg)}" if msg else keys
    style = "class:status.err" if is_error else ("class:status.ok" if msg and not is_error else "class:status")
    return FT([(style, _pad(text, width))])


# ─────────────────────────────────────────────
# Modal renderers
# ─────────────────────────────────────────────
def render_connect_modal(inputs: dict, cursor: int, error: str, width: int) -> FormattedText:
    fields = ["proto", "host", "port", "user", "password", "keypath", "bmname"]
    labels = [
        "Protocol (SFTP / SCP / FTP / FTPS)",
        "Host",
        "Port",
        "Username",
        "Password",
        "SSH Key path  (optional)",
        "Save as bookmark  (optional)",
    ]
    W = min(width - 4, 70)
    frags = []

    def line(style, text):
        frags.append((style, _pad(text, W) + "\n"))

    line("class:modal.title", " New Connection")
    line("class:modal.bg",    " " + "-" * (W - 2))

    for i, (f, lbl) in enumerate(zip(fields, labels)):
        active = i == cursor
        line("class:modal.label", f"  {lbl}")
        val = inputs[f].text if f != "password" else "*" * len(inputs[f].text)
        indicator = ">" if active else " "
        st = "class:modal.active" if active else "class:modal.bg"
        line(st, f" {indicator} {val}")

    line("class:modal.bg", " " + "-" * (W - 2))
    if error:
        line("class:status.err", f"  {error}")
    line("class:modal.key", "  Tab/Down next   Enter connect   Esc cancel")
    return FT(frags)


def render_bookmarks_modal(bookmarks: list, cursor: int, width: int) -> FormattedText:
    W = min(width - 4, 70)
    frags = []

    def line(style, text):
        frags.append((style, _pad(text, W) + "\n"))

    line("class:modal.title", " Bookmarks")
    line("class:modal.bg",    " " + "-" * (W - 2))

    if not bookmarks:
        line("class:modal.label", "  No bookmarks saved yet.")
    else:
        for i, b in enumerate(bookmarks):
            active = i == cursor
            indicator = ">" if active else " "
            st = "class:modal.active" if active else "class:modal.bg"
            label = f"{b['protocol']}  {b['user']}@{b['host']}:{b['port']}  [{b['name']}]"
            line(st, f" {indicator} {_safe(label)}")

    line("class:modal.bg",  " " + "-" * (W - 2))
    line("class:modal.key", "  Up/Down move   Enter connect   Esc cancel")
    return FT(frags)


def render_input_modal(prompt: str, value: str, width: int) -> FormattedText:
    W = min(width - 4, 70)
    frags = []

    def line(style, text):
        frags.append((style, _pad(text, W) + "\n"))

    line("class:modal.title", f" {prompt}")
    line("class:modal.bg",    " " + "-" * (W - 2))
    line("class:modal.active", f" > {_safe(value)}_")
    line("class:modal.bg",    " " + "-" * (W - 2))
    line("class:modal.key",   "  Enter confirm   Esc cancel")
    return FT(frags)


# ─────────────────────────────────────────────
# Main App
# ─────────────────────────────────────────────
class FtuiApp:
    PANE_HEIGHT = 28

    def __init__(self):
        self.left   = PaneState(is_local=True)
        self.right  = PaneState(is_local=False)
        self.focus  = "left"
        self.xfer   = XferState()
        self.client = None
        self._pool  = ThreadPoolExecutor(max_workers=2)
        self._app: Optional[Application] = None

        # status
        self._status_msg   = ""
        self._status_error = False

        # modal
        self.modal         = None   # None | "connect" | "bookmarks" | "mkdir" | "rename" | "confirm"
        self.modal_inputs: dict[str, TextArea] = {}
        self.modal_cursor  = 0
        self.modal_bms: list[dict] = []
        self.modal_prompt  = ""
        self.modal_cb      = None
        self.modal_error   = ""
        self._input_buf    = ""     # for input/confirm modals (typed chars)

        self._build()

    # ─── Build ────────────────────────────────

    def _build(self):
        def _pane_w() -> int:
            return 45

        def left_ft():
            w = _pane_w()
            if self.modal == "connect":
                return render_connect_modal(self.modal_inputs, self.modal_cursor, self.modal_error, w)
            if self.modal == "bookmarks":
                return render_bookmarks_modal(self.modal_bms, self.modal_cursor, w)
            if self.modal in ("mkdir", "rename", "confirm"):
                return render_input_modal(self.modal_prompt, self._input_buf, w)
            return render_pane(self.left, self.focus == "left", w, self.PANE_HEIGHT)

        def right_ft():
            w = _pane_w()
            return render_pane(self.right, self.focus == "right", w, self.PANE_HEIGHT)

        def header_ft():
            return FT([("class:header", _pad("  ftui  —  FTP / FTPS / SFTP / SCP", 91))])

        def status_ft():
            return render_status(self._status_msg, self._status_error, 91)

        def xfer_ft():
            return render_transfer(self.xfer, 91)

        lc = FormattedTextControl(left_ft,  focusable=True)
        rc = FormattedTextControl(right_ft, focusable=True)
        xc = FormattedTextControl(xfer_ft,   focusable=False)
        sc = FormattedTextControl(status_ft, focusable=False)
        hc = FormattedTextControl(header_ft, focusable=False)

        self._layout = Layout(HSplit([
            Window(hc, height=1, dont_extend_height=True),
            VSplit([
                Window(lc, width=45, dont_extend_height=False),
                Window(width=1, char="│", style="class:sep"),
                Window(rc, width=45, dont_extend_height=False),
            ]),
            ConditionalContainer(
                Window(xc, height=1, dont_extend_height=True),
                filter=Condition(lambda: self.xfer.active),
            ),
            Window(sc, height=1, dont_extend_height=True),
        ]))

        self._app = Application(
            layout=self._layout,
            key_bindings=self._build_keys(),
            style=STYLE,
            full_screen=False,
            mouse_support=False,
        )

    def _build_keys(self) -> KeyBindings:
        kb = KeyBindings()
        modal_open = Condition(lambda: self.modal is not None)
        no_modal   = Condition(lambda: self.modal is None)

        # ── Global nav (no modal) ──
        @kb.add("tab", filter=no_modal)
        def _tab(_):
            self.focus = "right" if self.focus == "left" else "left"
            self._redraw()

        @kb.add("up", filter=no_modal)
        def _up(_):
            self._active().move(-1, self.PANE_HEIGHT - 3)
            self._redraw()

        @kb.add("down", filter=no_modal)
        def _dn(_):
            self._active().move(1, self.PANE_HEIGHT - 3)
            self._redraw()

        @kb.add("pageup", filter=no_modal)
        def _pgup(_):
            self._active().move(-(self.PANE_HEIGHT - 4), self.PANE_HEIGHT - 3)
            self._redraw()

        @kb.add("pagedown", filter=no_modal)
        def _pgdn(_):
            self._active().move(self.PANE_HEIGHT - 4, self.PANE_HEIGHT - 3)
            self._redraw()

        @kb.add("enter", filter=no_modal)
        def _enter(_):
            new_path = self._active().enter_path()
            if new_path:
                self._load(self._active(), new_path)

        @kb.add("f2", filter=no_modal)
        def _f2(_): self._open_connect()

        @kb.add("f3", filter=no_modal)
        def _f3(_): self._open_bookmarks()

        @kb.add("f5", filter=no_modal)
        def _f5(_): self._do_transfer()

        @kb.add("f7", filter=no_modal)
        def _f7(_): self._open_input("mkdir", "New directory name:")

        @kb.add("f8", filter=no_modal)
        def _f8(_):
            e = self._active().selected()
            if e and e.name != "..":
                self._open_input("confirm", f"Delete '{_safe(e.name)}'?  Type YES to confirm:")

        @kb.add("f9", filter=no_modal)
        def _f9(_):
            e = self._active().selected()
            if e and e.name != "..":
                self._input_buf = e.name
                self._open_input("rename", f"Rename '{_safe(e.name)}' to:")

        @kb.add("q", filter=no_modal)
        @kb.add("Q", filter=no_modal)
        def _quit(event): event.app.exit()

        # ── Escape closes any modal ──
        @kb.add("escape")
        def _esc(_):
            self.modal = None
            self._input_buf = ""
            self._redraw()

        # ── Connect modal navigation ──
        @kb.add("tab",   filter=Condition(lambda: self.modal == "connect"))
        @kb.add("down",  filter=Condition(lambda: self.modal == "connect"))
        def _cm_next(_):
            self.modal_cursor = (self.modal_cursor + 1) % 7
            self._focus_connect_field()
            self._redraw()

        @kb.add("s-tab", filter=Condition(lambda: self.modal == "connect"))
        @kb.add("up",    filter=Condition(lambda: self.modal == "connect"))
        def _cm_prev(_):
            self.modal_cursor = (self.modal_cursor - 1) % 7
            self._focus_connect_field()
            self._redraw()

        @kb.add("enter", filter=Condition(lambda: self.modal == "connect"))
        def _cm_enter(_): self._do_connect()

        # ── Bookmarks modal ──
        @kb.add("up",    filter=Condition(lambda: self.modal == "bookmarks"))
        def _bm_up(_):
            if self.modal_bms:
                self.modal_cursor = (self.modal_cursor - 1) % len(self.modal_bms)
            self._redraw()

        @kb.add("down",  filter=Condition(lambda: self.modal == "bookmarks"))
        def _bm_dn(_):
            if self.modal_bms:
                self.modal_cursor = (self.modal_cursor + 1) % len(self.modal_bms)
            self._redraw()

        @kb.add("enter", filter=Condition(lambda: self.modal == "bookmarks"))
        def _bm_enter(_): self._connect_bookmark()

        # ── Input/confirm modals: typed chars ──
        input_active = Condition(lambda: self.modal in ("mkdir", "rename", "confirm"))

        @kb.add("enter", filter=input_active)
        def _inp_enter(_):
            val = self._input_buf
            cb  = self.modal_cb
            self.modal = None
            self._input_buf = ""
            self._redraw()
            if cb:
                cb(val)

        @kb.add("backspace", filter=input_active)
        def _inp_bs(_):
            self._input_buf = self._input_buf[:-1]
            self._redraw()

        # catch all printable chars for input modals
        for ch in (
            "abcdefghijklmnopqrstuvwxyz"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "0123456789"
            " ._-()[]{}@#!$%^&+=,~"
        ):
            @kb.add(ch, filter=input_active)
            def _inp_char(event, c=ch):
                self._input_buf += c
                self._redraw()

        return kb

    # ─── Helpers ──────────────────────────────

    def _active(self) -> PaneState:
        return self.left if self.focus == "left" else self.right

    def _inactive(self) -> PaneState:
        return self.right if self.focus == "left" else self.left

    def _redraw(self):
        if self._app:
            self._app.invalidate()

    def _status(self, msg: str, error: bool = False):
        self._status_msg   = msg
        self._status_error = error
        self._redraw()

    # ─── Directory loading ─────────────────────

    def _load(self, state: PaneState, path: str):
        state.loading = True
        state.error   = ""
        self._redraw()

        def _do():
            try:
                if state.is_local:
                    p = Path(path)
                    entries = []
                    for item in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                        try:
                            st = item.stat()
                            entries.append(FileEntry(name=item.name, size=st.st_size, is_dir=item.is_dir()))
                        except PermissionError:
                            pass
                    state.path    = str(p)
                    state.entries = entries
                else:
                    new_path      = state.client.cd(path)
                    state.entries = state.client.ls(new_path)
                    state.path    = new_path
                state.cursor = 0
                state.offset = 0
            except Exception as e:
                state.error = str(e)
                self._status(f"Error: {e}", error=True)
            finally:
                state.loading = False
                self._redraw()

        self._pool.submit(_do)

    # ─── Connect ──────────────────────────────

    def _open_connect(self):
        self.modal = "connect"
        self.modal_cursor = 0
        self.modal_error  = ""
        fields = ["proto", "host", "port", "user", "password", "keypath", "bmname"]
        defaults = {"proto": "SFTP", "port": "22"}
        self.modal_inputs = {
            f: TextArea(text=defaults.get(f, ""), multiline=False, height=1,
                        password=(f == "password"))
            for f in fields
        }
        self._focus_connect_field()
        self._redraw()

    def _focus_connect_field(self):
        fields = ["proto", "host", "port", "user", "password", "keypath", "bmname"]
        key = fields[self.modal_cursor]
        if self._app:
            try:
                self._app.layout.focus(self.modal_inputs[key])
            except Exception:
                pass

    def _do_connect(self):
        i = self.modal_inputs
        proto    = i["proto"].text.strip().upper() or "SFTP"
        host     = i["host"].text.strip()
        port_str = i["port"].text.strip()
        user     = i["user"].text.strip()
        password = i["password"].text or None
        keypath  = i["keypath"].text.strip() or None
        bmname   = i["bmname"].text.strip() or None

        if not host:
            self.modal_error = "Host is required"
            self._redraw()
            return
        if not user:
            self.modal_error = "Username is required"
            self._redraw()
            return
        try:
            port = int(port_str)
        except ValueError:
            self.modal_error = "Invalid port"
            self._redraw()
            return

        self.modal = None
        self._status(f"Connecting to {host}...")

        def _thread():
            try:
                c = connect(proto, host, port, user, password, keypath)
                if bmname:
                    bm.save_bookmark(bmname, proto, host, port, user, password, keypath)
                self.client = c
                self.right.client    = c
                self.right.connected = True
                self._load(self.right, c.cwd)
                self._status(f"Connected via {c.protocol} to {host}")
            except Exception as e:
                self._status(f"Connection failed: {e}", error=True)

        self._pool.submit(_thread)

    # ─── Bookmarks ────────────────────────────

    def _open_bookmarks(self):
        self.modal = "bookmarks"
        self.modal_bms    = bm.list_bookmarks()
        self.modal_cursor = 0
        self._redraw()

    def _connect_bookmark(self):
        if not self.modal_bms:
            self.modal = None
            self._redraw()
            return
        b = self.modal_bms[self.modal_cursor]
        self.modal = None
        self._status(f"Connecting to {b['host']}...")

        def _thread():
            try:
                c = connect(b["protocol"], b["host"], b["port"],
                            b["user"], b.get("password"), b.get("key_path"))
                self.client = c
                self.right.client    = c
                self.right.connected = True
                remote_path = b.get("remote_path", "/")
                self._load(self.right, remote_path)
                self._status(f"Connected via {c.protocol} to {b['host']}")
            except Exception as e:
                self._status(f"Connection failed: {e}", error=True)

        self._pool.submit(_thread)

    # ─── Input modals ─────────────────────────

    def _open_input(self, kind: str, prompt: str):
        if kind != "rename":
            self._input_buf = ""
        self.modal        = kind
        self.modal_prompt = prompt
        self.modal_cb     = {
            "mkdir":   self._do_mkdir,
            "rename":  self._do_rename,
            "confirm": self._do_delete_confirmed,
        }[kind]
        self._redraw()

    # ─── Operations ───────────────────────────

    def _do_mkdir(self, name: str):
        name = name.strip()
        if not name:
            return
        pane = self._active()
        def _th():
            try:
                if pane.is_local:
                    Path(pane.path, name).mkdir()
                else:
                    self.client.mkdir(posixpath.join(pane.path, name))
                self._load(pane, pane.path)
                self._status(f"Created: {name}")
            except Exception as e:
                self._status(f"Mkdir failed: {e}", error=True)
        self._pool.submit(_th)

    def _do_rename(self, new_name: str):
        new_name = new_name.strip()
        pane  = self._active()
        entry = pane.selected()
        if not entry or not new_name or new_name == entry.name:
            return
        def _th():
            try:
                if pane.is_local:
                    Path(pane.path, entry.name).rename(Path(pane.path, new_name))
                else:
                    old = posixpath.join(pane.path, entry.name)
                    new = posixpath.join(pane.path, new_name)
                    self.client.rename(old, new)
                self._load(pane, pane.path)
                self._status(f"Renamed to: {new_name}")
            except Exception as e:
                self._status(f"Rename failed: {e}", error=True)
        self._pool.submit(_th)

    def _do_delete_confirmed(self, value: str):
        if value.strip().upper() != "YES":
            self._status("Delete cancelled.")
            return
        pane  = self._active()
        entry = pane.selected()
        if not entry or entry.name == "..":
            return
        def _th():
            try:
                if pane.is_local:
                    target = Path(pane.path) / entry.name
                    shutil.rmtree(target) if entry.is_dir else target.unlink()
                else:
                    path = posixpath.join(pane.path, entry.name)
                    if entry.is_dir:
                        self._del_remote_dir(path)
                    else:
                        self.client.delete(path, is_dir=False)
                self._load(pane, pane.path)
                self._status(f"Deleted: {entry.name}")
            except Exception as e:
                self._status(f"Delete failed: {e}", error=True)
        self._pool.submit(_th)

    def _del_remote_dir(self, path: str):
        for e in self.client.ls(path):
            child = posixpath.join(path, e.name)
            if e.is_dir:
                self._del_remote_dir(child)
            else:
                self.client.delete(child, is_dir=False)
        self.client.delete(path, is_dir=True)

    # ─── Transfer ─────────────────────────────

    def _do_transfer(self):
        if not self.client:
            self._status("Not connected.", error=True)
            return
        if self.xfer.active:
            self._status("Transfer in progress.", error=True)
            return

        src   = self._active()
        dst   = self._inactive()
        entry = src.selected()
        if not entry or entry.name == "..":
            self._status("Select a file or directory.")
            return

        direction = "UP" if src.is_local else "DN"
        if src.is_local:
            local_path  = os.path.join(src.path, entry.name)
            remote_path = posixpath.join(dst.path, entry.name)
        else:
            remote_path = posixpath.join(src.path, entry.name)
            local_path  = os.path.join(dst.path, entry.name)

        x = self.xfer
        x.reset()
        x.active     = True
        x.name       = entry.name
        x.direction  = direction
        x.total      = entry.size
        x.is_dir     = entry.is_dir

        last_ui = [0.0]

        def _maybe_redraw():
            now = time.monotonic()
            if now - last_ui[0] > 0.15:
                last_ui[0] = now
                self._redraw()

        def _cb(transferred: int, total: int):
            x.transferred = transferred
            if total > 0:
                x.total = total
            _maybe_redraw()

        def _file_cb(transferred: int, total: int):
            x.transferred = transferred
            if total > 0 and transferred >= total:
                x.done_files += 1
            _maybe_redraw()

        def _th():
            try:
                if entry.is_dir:
                    if direction == "UP":
                        x.total_files = sum(1 for p in Path(local_path).rglob("*") if p.is_file())
                        self.client.upload_dir(local_path, remote_path, _file_cb)
                    else:
                        self.client.download_dir(remote_path, local_path, _file_cb)
                else:
                    if direction == "UP":
                        self.client.upload(local_path, remote_path, _cb)
                    else:
                        self.client.download(remote_path, local_path, _cb)

                x.transferred = x.total
                self._redraw()
                time.sleep(0.4)
                x.active = False
                self._load(dst, dst.path)
                verb = "Uploaded" if direction == "UP" else "Downloaded"
                self._status(f"{verb}: {entry.name}")
            except Exception as e:
                x.active = False
                self._status(f"Transfer failed: {e}", error=True)
                self._redraw()

        self._pool.submit(_th)
        self._redraw()

    # ─── Run ──────────────────────────────────

    def run(self):
        self._load(self.left, self.left.path)
        self._app.run()


def main():
    FtuiApp().run()


if __name__ == "__main__":
    main()
