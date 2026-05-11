"""
ftui - dual-pane TUI file transfer client
Backend: prompt_toolkit (FormattedText tuple API)
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
from prompt_toolkit.formatted_text import FormattedText
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

from ftui.protocols import FileEntry, connect
from ftui import bookmarks as bm

socket.setdefaulttimeout(15)

FT = FormattedText

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

PROTOCOLS     = ["SFTP", "SCP", "FTP", "FTPS"]
DEFAULT_PORTS = {"SFTP": "22", "SCP": "22", "FTP": "21", "FTPS": "21"}
CONNECT_FIELDS = ["proto", "host", "port", "user", "password", "keypath", "bmname"]
CONNECT_LABELS = [
    "Protocol  (Space=cycle  1=SFTP 2=SCP 3=FTP 4=FTPS)",
    "Host",
    "Port",
    "Username",
    "Password",
    "SSH Key path  (optional)",
    "Bookmark name (optional)",
]
PANE_W  = 45
PANE_H  = 28


def _safe(s: str) -> str:
    return "".join(c if c >= " " else "?" for c in s)

def _pad(s: str, w: int) -> str:
    return s[:w-1] + "~" if len(s) > w else s.ljust(w)

def _rpad(s: str, w: int) -> str:
    return s.rjust(w)


# ─── Pane state ───────────────────────────────

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

    def _all(self) -> list[FileEntry]:
        pre = [] if self.path in ("/", "") else [FileEntry(name="..", size=0, is_dir=True)]
        return pre + self.entries

    def selected(self) -> Optional[FileEntry]:
        all_ = self._all()
        return all_[self.cursor] if 0 <= self.cursor < len(all_) else None

    def move(self, delta: int, vis_h: int):
        total = len(self._all())
        if not total:
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
            return str(Path(self.path).parent) if self.is_local else \
                   posixpath.dirname(self.path.rstrip("/")) or "/"
        return os.path.join(self.path, e.name) if self.is_local else \
               posixpath.join(self.path, e.name)


# ─── Transfer state ───────────────────────────

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

    def bar_ft(self, width: int) -> list:
        filled = int(width * self.pct / 100)
        return [
            ("class:xfer.bar",  "█" * filled),
            ("class:xfer.fill", "░" * (width - filled)),
        ]


# ─── Renderers ────────────────────────────────

def render_pane(state: PaneState, focused: bool, width: int, height: int) -> FormattedText:
    frags = []
    W = width
    name_w = W - 11

    # Title
    if state.is_local:
        title = "Local"
    elif state.connected:
        title = f"Remote  [{state.client.protocol}]"
    else:
        title = "Remote  [not connected]"
    if state.loading:
        title += "  ..."
    t_style = "class:pane.title.f" if focused else "class:pane.title"
    frags.append((t_style, _pad(f" {title}", W) + "\n"))

    # Path
    path = _safe(state.path)
    if len(path) > W - 2:
        path = "..." + path[-(W-5):]
    frags.append(("class:pane.path", _pad(f" {path}", W) + "\n"))

    # Column header
    frags.append(("class:pane.hdr",
                  _pad(f" {'Name'}", name_w + 1) + _rpad("Size ", 10) + "\n"))

    # Entries
    all_entries = state._all()
    vis_h = height - 3
    visible = all_entries[state.offset: state.offset + vis_h]

    for i, e in enumerate(visible):
        abs_i  = state.offset + i
        is_cur = abs_i == state.cursor
        alt    = abs_i % 2 == 1

        if is_cur:
            style = "class:pane.cursor"
        elif e.is_dir:
            style = "class:pane.dir.alt" if alt else "class:pane.dir"
        else:
            style = "class:pane.file.alt" if alt else "class:pane.file"

        marker   = ">" if is_cur else " "
        prefix   = "DIR " if e.is_dir else "    "
        name_str = _pad(f"{marker}{prefix}{_safe(e.name)}", name_w + 1)
        size_str = _rpad(e.size_human, 9)
        frags.append((style, name_str + size_str + "\n"))

    for _ in range(vis_h - len(visible)):
        frags.append(("class:pane.empty", " " * W + "\n"))

    return FT(frags)


def render_transfer(xfer: XferState, width: int) -> FormattedText:
    bar_w = max(10, width - 50)
    if xfer.is_dir:
        label = f" {xfer.direction}  {_safe(xfer.name)}/  [{xfer.done_files}/{xfer.total_files} files]  "
    else:
        label = f" {xfer.direction}  {_safe(xfer.name)}  {xfer.transferred//1024}/{xfer.total//1024} KB  "
    frags = [("class:xfer", label)]
    frags.extend(xfer.bar_ft(bar_w))
    frags.append(("class:xfer", f"  {xfer.pct}%\n"))
    return FT(frags)


def render_status(msg: str, is_error: bool, width: int) -> FormattedText:
    keys = " F2/c Connect  F3/b Bookmarks  F5/t Transfer  F7/m Mkdir  F8/d Delete  F9/r Rename  Tab  Q Quit"
    frags = []
    if msg:
        style = "class:status.err" if is_error else "class:status.ok"
        frags.append((style, _pad(f" {_safe(msg)}", width) + "\n"))
    frags.append(("class:status", _pad(keys, width)))
    return FT(frags)


def render_connect_modal(inputs: dict, cursor: int, error: str, width: int) -> FormattedText:
    W = min(width - 2, 68)
    frags = []

    def line(style, text):
        frags.append((style, _pad(text, W) + "\n"))

    line("class:modal.title", " New Connection")
    line("class:modal.bg",    " " + "-" * (W - 2))

    for i, (f, lbl) in enumerate(zip(CONNECT_FIELDS, CONNECT_LABELS)):
        active = i == cursor
        line("class:modal.label", f"  {lbl}")
        val = "*" * len(inputs[f]) if f == "password" else \
              f"[ {inputs[f]} ]"    if f == "proto"    else \
              inputs[f] + ("_" if active else "")
        st = "class:modal.active" if active else "class:modal.bg"
        line(st, f" {'>' if active else ' '} {_safe(val)}")

    line("class:modal.bg",  " " + "-" * (W - 2))
    if error:
        line("class:status.err", f"  {error}")
    line("class:modal.key", "  Tab next   Enter connect   Esc cancel")
    return FT(frags)


def render_bookmarks_modal(bookmarks: list, cursor: int, width: int) -> FormattedText:
    W = min(width - 2, 68)
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
            label  = f"{b['protocol']}  {b['user']}@{b['host']}:{b['port']}  [{b['name']}]"
            st = "class:modal.active" if active else "class:modal.bg"
            line(st, f" {'>' if active else ' '} {_safe(label)}")

    line("class:modal.bg",  " " + "-" * (W - 2))
    line("class:modal.key", "  Up/Down   Enter connect   Esc cancel")
    return FT(frags)


def render_input_modal(prompt: str, value: str, width: int) -> FormattedText:
    W = min(width - 2, 68)
    frags = []

    def line(style, text):
        frags.append((style, _pad(text, W) + "\n"))

    line("class:modal.title", f" {_safe(prompt)}")
    line("class:modal.bg",    " " + "-" * (W - 2))
    line("class:modal.active", f" > {_safe(value)}_")
    line("class:modal.bg",    " " + "-" * (W - 2))
    line("class:modal.key",   "  Enter confirm   Esc cancel")
    return FT(frags)


# ─── App ──────────────────────────────────────

class FtuiApp:
    def __init__(self):
        self.left   = PaneState(is_local=True)
        self.right  = PaneState(is_local=False)
        self.focus  = "left"
        self.xfer   = XferState()
        self.client = None
        self._pool  = ThreadPoolExecutor(max_workers=2)
        self._app: Optional[Application] = None

        self._status_msg   = ""
        self._status_error = False

        self.modal         = None
        self.modal_cursor  = 0
        self.modal_error   = ""
        self.modal_bms: list[dict] = []
        self.modal_inputs: dict[str, str] = {}
        self.modal_prompt  = ""
        self.modal_cb      = None
        self._input_buf    = ""

        self._build()

    def _build(self):
        def left_ft():
            if self.modal == "connect":
                return render_connect_modal(self.modal_inputs, self.modal_cursor, self.modal_error, PANE_W)
            if self.modal == "bookmarks":
                return render_bookmarks_modal(self.modal_bms, self.modal_cursor, PANE_W)
            if self.modal in ("mkdir", "rename", "confirm"):
                return render_input_modal(self.modal_prompt, self._input_buf, PANE_W)
            return render_pane(self.left, self.focus == "left", PANE_W, PANE_H)

        def right_ft():
            return render_pane(self.right, self.focus == "right", PANE_W, PANE_H)

        def xfer_ft():
            return render_transfer(self.xfer, PANE_W * 2 + 1)

        def status_ft():
            return render_status(self._status_msg, self._status_error, PANE_W * 2 + 1)

        def header_ft():
            return FT([("class:header", _pad("  ftui  —  FTP / FTPS / SFTP / SCP", PANE_W * 2 + 1))])

        # 1. All dynamic text wrapped in controls
        lc = FormattedTextControl(left_ft,  focusable=True)
        rc = FormattedTextControl(right_ft, focusable=True)
        hc = FormattedTextControl(header_ft)
        xc = FormattedTextControl(xfer_ft)
        sc = FormattedTextControl(status_ft)

        # 2. Layout uses the controls
        self._layout = Layout(HSplit([
            Window(hc, height=1, dont_extend_height=True),
            VSplit([
                Window(lc, width=PANE_W, dont_extend_height=False),
                Window(width=1, char="│", style="class:sep"),
                Window(rc, width=PANE_W, dont_extend_height=False),
            ]),
            ConditionalContainer(
                Window(xc, height=1, dont_extend_height=True),
                filter=Condition(lambda: self.xfer.active),
            ),
            Window(sc,
                   height=lambda: 2 if self._status_msg else 1,
                   dont_extend_height=True),
        ]))

        # 3. ---> THIS WAS MISSING <---
        # Re-assign the actual Application object
        self._app = Application(
            layout=self._layout,
            key_bindings=self._build_keys(),
            style=STYLE,
            full_screen=False,
            mouse_support=False,
        )

    def _build_keys(self) -> KeyBindings:
        kb       = KeyBindings()
        no_modal = Condition(lambda: self.modal is None)
        in_conn  = Condition(lambda: self.modal == "connect")
        in_bm    = Condition(lambda: self.modal == "bookmarks")
        in_inp   = Condition(lambda: self.modal in ("mkdir", "rename", "confirm"))
        on_proto = Condition(lambda: self.modal == "connect" and self.modal_cursor == 0)
        in_field = Condition(lambda: self.modal == "connect" and self.modal_cursor != 0)

        # ── Navigazione principale ─────────────
        @kb.add("up",       filter=no_modal)
        def _up(_): self._active().move(-1, PANE_H - 3); self._redraw()

        @kb.add("down",     filter=no_modal)
        def _dn(_): self._active().move(1,  PANE_H - 3); self._redraw()

        @kb.add("pageup",   filter=no_modal)
        def _pu(_): self._active().move(-(PANE_H-4), PANE_H-3); self._redraw()

        @kb.add("pagedown", filter=no_modal)
        def _pd(_): self._active().move(PANE_H-4,    PANE_H-3); self._redraw()

        @kb.add("enter",    filter=no_modal)
        def _enter(_):
            p = self._active().enter_path()
            if p:
                self._load(self._active(), p)

        @kb.add("tab",      filter=no_modal)
        def _tab(_):
            self.focus = "right" if self.focus == "left" else "left"
            self._redraw()

        @kb.add("q",        filter=no_modal)
        @kb.add("Q",        filter=no_modal)
        def _quit(e): e.app.exit()

        # ── Azioni ────────────────────────────
        @kb.add("f2",  filter=no_modal)
        @kb.add("c",   filter=no_modal)
        def _f2(_): self._open_connect()

        @kb.add("f3",  filter=no_modal)
        @kb.add("b",   filter=no_modal)
        def _f3(_): self._open_bookmarks()

        @kb.add("f5",  filter=no_modal)
        @kb.add("t",   filter=no_modal)
        def _f5(_): self._do_transfer()

        @kb.add("f7",  filter=no_modal)
        @kb.add("m",   filter=no_modal)
        def _f7(_): self._open_input("mkdir", "New directory name:")

        @kb.add("f8",  filter=no_modal)
        @kb.add("d",   filter=no_modal)
        def _f8(_):
            e = self._active().selected()
            if e and e.name != "..":
                self._open_input("confirm", f"Delete '{_safe(e.name)}'?  Type YES:")

        @kb.add("f9",  filter=no_modal)
        @kb.add("r",   filter=no_modal)
        def _f9(_):
            e = self._active().selected()
            if e and e.name != "..":
                self._open_input("rename", f"Rename '{_safe(e.name)}' to:", e.name)

        # ── Escape chiude qualsiasi modal ─────
        @kb.add("escape")
        def _esc(_):
            self.modal      = None
            self._input_buf = ""
            self._redraw()

        # ── Connect modal ─────────────────────
        @kb.add("tab",   filter=in_conn)
        @kb.add("down",  filter=in_conn)
        def _cn(_):
            self.modal_cursor = (self.modal_cursor + 1) % len(CONNECT_FIELDS)
            self._redraw()

        @kb.add("s-tab", filter=in_conn)
        @kb.add("up",    filter=in_conn)
        def _cp(_):
            self.modal_cursor = (self.modal_cursor - 1) % len(CONNECT_FIELDS)
            self._redraw()

        @kb.add("enter", filter=in_conn)
        def _ce(_): self._do_connect()

        @kb.add("backspace", filter=in_field)
        def _cb(_):
            f = CONNECT_FIELDS[self.modal_cursor]
            self.modal_inputs[f] = self.modal_inputs[f][:-1]
            self._redraw()

        def _set_proto(p):
            self.modal_inputs["proto"] = p
            self.modal_inputs["port"]  = DEFAULT_PORTS[p]
            self._redraw()

        @kb.add("space", filter=on_proto)
        def _ps(_):
            cur = self.modal_inputs["proto"].upper()
            idx = PROTOCOLS.index(cur) if cur in PROTOCOLS else 0
            _set_proto(PROTOCOLS[(idx + 1) % len(PROTOCOLS)])

        @kb.add("1", filter=on_proto)
        def _p1(_): _set_proto("SFTP")
        @kb.add("2", filter=on_proto)
        def _p2(_): _set_proto("SCP")
        @kb.add("3", filter=on_proto)
        def _p3(_): _set_proto("FTP")
        @kb.add("4", filter=on_proto)
        def _p4(_): _set_proto("FTPS")

        for ch in ("abcdefghijklmnopqrstuvwxyz"
                   "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                   "0123456789._-@/:[]{}()#!$%^&+=,~? "):
            @kb.add(ch, filter=in_field)
            def _cc(event, c=ch):
                f = CONNECT_FIELDS[self.modal_cursor]
                self.modal_inputs[f] += c
                self._redraw()

        # ── Bookmarks modal ───────────────────
        @kb.add("up",    filter=in_bm)
        def _bu(_):
            if self.modal_bms:
                self.modal_cursor = (self.modal_cursor - 1) % len(self.modal_bms)
            self._redraw()

        @kb.add("down",  filter=in_bm)
        def _bd(_):
            if self.modal_bms:
                self.modal_cursor = (self.modal_cursor + 1) % len(self.modal_bms)
            self._redraw()

        @kb.add("enter", filter=in_bm)
        def _be(_): self._connect_bookmark()

        # ── Input modal ───────────────────────
        @kb.add("enter",     filter=in_inp)
        def _ie(_):
            val = self._input_buf; cb = self.modal_cb
            self.modal = None; self._input_buf = ""
            self._redraw()
            if cb: cb(val)

        @kb.add("backspace", filter=in_inp)
        def _ib(_):
            self._input_buf = self._input_buf[:-1]
            self._redraw()

        for ch in ("abcdefghijklmnopqrstuvwxyz"
                   "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                   "0123456789._-@/:[]{}()#!$%^&+=,~? "):
            @kb.add(ch, filter=in_inp)
            def _ic(event, c=ch):
                self._input_buf += c
                self._redraw()

        return kb

    # ─── Helpers ──────────────────────────────

    def _active(self)   -> PaneState: return self.left  if self.focus == "left"  else self.right
    def _inactive(self) -> PaneState: return self.right if self.focus == "left"  else self.left

    def _redraw(self):
        if self._app: self._app.invalidate()

    def _status(self, msg: str, error: bool = False):
        self._status_msg   = msg
        self._status_error = error
        self._redraw()

    # ─── Directory loading ─────────────────────

    def _load(self, state: PaneState, path: str):
        state.loading = True
        self._redraw()

        def _do():
            try:
                if state.is_local:
                    p = Path(path)
                    entries = []
                    for item in sorted(p.iterdir(),
                                       key=lambda x: (not x.is_dir(follow_symlinks=True), x.name.lower())):
                        try:
                            st = item.stat(follow_symlinks=True)
                            entries.append(FileEntry(
                                name=item.name,
                                size=st.st_size,
                                is_dir=item.is_dir(follow_symlinks=True),
                            ))
                        except OSError:
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
                self._status(f"Error: {e}", error=True)
            finally:
                state.loading = False
                self._redraw()

        self._pool.submit(_do)

    # ─── Connect ──────────────────────────────

    def _open_connect(self):
        self.modal        = "connect"
        self.modal_cursor = 0
        self.modal_error  = ""
        self.modal_inputs = {f: ("SFTP" if f == "proto" else "22" if f == "port" else "")
                             for f in CONNECT_FIELDS}
        self._redraw()

    def _do_connect(self):
        i        = self.modal_inputs
        proto    = i["proto"].strip().upper() or "SFTP"
        host     = i["host"].strip()
        port_str = i["port"].strip()
        user     = i["user"].strip()
        password = i["password"] or None
        keypath  = i["keypath"].strip() or None
        bmname   = i["bmname"].strip() or None

        if not host:   self.modal_error = "Host is required";   self._redraw(); return
        if not user:   self.modal_error = "Username is required"; self._redraw(); return
        try:           port = int(port_str)
        except ValueError: self.modal_error = "Invalid port"; self._redraw(); return

        self.modal = None
        self._status(f"Connecting to {host}...")

        def _th():
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

        self._pool.submit(_th)

    # ─── Bookmarks ────────────────────────────

    def _open_bookmarks(self):
        self.modal        = "bookmarks"
        self.modal_bms    = bm.list_bookmarks()
        self.modal_cursor = 0
        self._redraw()

    def _connect_bookmark(self):
        if not self.modal_bms:
            self.modal = None; self._redraw(); return
        b = self.modal_bms[self.modal_cursor]
        self.modal = None
        self._status(f"Connecting to {b['host']}...")

        def _th():
            try:
                c = connect(b["protocol"], b["host"], b["port"],
                            b["user"], b.get("password"), b.get("key_path"))
                self.client = c
                self.right.client    = c
                self.right.connected = True
                self._load(self.right, b.get("remote_path", "/"))
                self._status(f"Connected via {c.protocol} to {b['host']}")
            except Exception as e:
                self._status(f"Connection failed: {e}", error=True)

        self._pool.submit(_th)

    # ─── Input modal ──────────────────────────

    def _open_input(self, kind: str, prompt: str, default: str = ""):
        self.modal        = kind
        self.modal_prompt = prompt
        self._input_buf   = default
        self.modal_cb     = {"mkdir": self._do_mkdir,
                             "rename": self._do_rename,
                             "confirm": self._do_delete_confirmed}[kind]
        self._redraw()

    # ─── Operazioni ───────────────────────────

    def _do_mkdir(self, name: str):
        name = name.strip()
        if not name: return
        pane = self._active()
        def _th():
            try:
                if pane.is_local: Path(pane.path, name).mkdir()
                else: self.client.mkdir(posixpath.join(pane.path, name))
                self._load(pane, pane.path)
                self._status(f"Created: {name}")
            except Exception as e:
                self._status(f"Mkdir failed: {e}", error=True)
        self._pool.submit(_th)

    def _do_rename(self, new_name: str):
        new_name = new_name.strip()
        pane = self._active(); entry = pane.selected()
        if not entry or not new_name or new_name == entry.name: return
        def _th():
            try:
                if pane.is_local:
                    Path(pane.path, entry.name).rename(Path(pane.path, new_name))
                else:
                    self.client.rename(posixpath.join(pane.path, entry.name),
                                       posixpath.join(pane.path, new_name))
                self._load(pane, pane.path)
                self._status(f"Renamed to: {new_name}")
            except Exception as e:
                self._status(f"Rename failed: {e}", error=True)
        self._pool.submit(_th)

    def _do_delete_confirmed(self, value: str):
        if value.strip().upper() != "YES":
            self._status("Delete cancelled."); return
        pane = self._active(); entry = pane.selected()
        if not entry or entry.name == "..": return
        def _th():
            try:
                if pane.is_local:
                    target = Path(pane.path) / entry.name
                    shutil.rmtree(target) if entry.is_dir else target.unlink()
                else:
                    path = posixpath.join(pane.path, entry.name)
                    self._del_remote_dir(path) if entry.is_dir else self.client.delete(path, is_dir=False)
                self._load(pane, pane.path)
                self._status(f"Deleted: {entry.name}")
            except Exception as e:
                self._status(f"Delete failed: {e}", error=True)
        self._pool.submit(_th)

    def _del_remote_dir(self, path: str):
        for e in self.client.ls(path):
            child = posixpath.join(path, e.name)
            self._del_remote_dir(child) if e.is_dir else self.client.delete(child, is_dir=False)
        self.client.delete(path, is_dir=True)

    # ─── Transfer ─────────────────────────────

    def _do_transfer(self):
        if not self.client:
            self._status("Not connected.", error=True); return
        if self.xfer.active:
            self._status("Transfer in progress.", error=True); return

        src = self._active(); dst = self._inactive()
        entry = src.selected()
        if not entry or entry.name == "..":
            self._status("Select a file or directory."); return

        direction   = "UP" if src.is_local else "DN"
        local_path  = os.path.join(src.path, entry.name)  if src.is_local else os.path.join(dst.path, entry.name)
        remote_path = posixpath.join(dst.path, entry.name) if src.is_local else posixpath.join(src.path, entry.name)

        x = self.xfer
        x.reset()
        x.active    = True
        x.name      = entry.name
        x.direction = direction
        x.total     = entry.size
        x.is_dir    = entry.is_dir

        last_ui = [0.0]

        def _maybe():
            now = time.monotonic()
            if now - last_ui[0] > 0.15:
                last_ui[0] = now; self._redraw()

        def _cb(t, tot):
            x.transferred = t
            if tot > 0: x.total = tot
            _maybe()

        def _fcb(t, tot):
            x.transferred = t
            if tot > 0 and t >= tot: x.done_files += 1
            _maybe()

        def _th():
            try:
                if entry.is_dir:
                    if direction == "UP":
                        x.total_files = sum(1 for p in Path(local_path).rglob("*") if p.is_file())
                        self.client.upload_dir(local_path, remote_path, _fcb)
                    else:
                        self.client.download_dir(remote_path, local_path, _fcb)
                else:
                    if direction == "UP": self.client.upload(local_path, remote_path, _cb)
                    else:                 self.client.download(remote_path, local_path, _cb)
                x.transferred = x.total; self._redraw()
                time.sleep(0.4)
                x.active = False
                self._load(dst, dst.path)
                self._status(f"{'Uploaded' if direction == 'UP' else 'Downloaded'}: {entry.name}")
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
