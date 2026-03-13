"""
ftui - dual-pane TUI file transfer client
Supports: FTP, FTPS, SFTP, SCP
"""

from __future__ import annotations

import os
import posixpath
import shutil
import threading
from pathlib import Path
from typing import Optional

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    Select,
    Static,
)

from ftui.protocols import FileEntry, connect
from ftui import bookmarks as bm


CSS = """
Screen {
    background: #0d1117;
}

Header {
    background: #161b22;
    color: #6ea8fe;
    text-style: bold;
}

Footer {
    background: #161b22;
    color: #6e7681;
}

#layout {
    layout: horizontal;
    height: 1fr;
}

.pane {
    width: 1fr;
    border: solid #21262d;
    margin: 0 1;
}

.pane:focus-within {
    border: solid #388bfd;
}

.pane-title {
    background: #161b22;
    color: #e6edf3;
    text-align: center;
    padding: 0 1;
    text-style: bold;
}

.pane-path {
    background: #0d1117;
    color: #388bfd;
    padding: 0 1;
}

DataTable {
    height: 1fr;
    background: #0d1117;
}

DataTable > .datatable--header {
    background: #161b22;
    color: #6e7681;
    text-style: bold;
}

DataTable > .datatable--cursor {
    background: #1f6feb;
    color: #ffffff;
}

DataTable > .datatable--hover {
    background: #1c2128;
}

DataTable > .datatable--zebra-stripe {
    background: #0d1117;
}

#transfer-bar {
    height: 3;
    background: #161b22;
    border-top: solid #21262d;
    padding: 0 2;
    display: none;
    layout: horizontal;
    align: left middle;
}

#transfer-bar.visible {
    display: block;
}

#transfer-label {
    color: #388bfd;
    width: auto;
    margin-right: 2;
}

ProgressBar {
    width: 1fr;
}

.status-bar {
    height: 1;
    background: #161b22;
    color: #6e7681;
    padding: 0 2;
}

.modal-screen {
    align: center middle;
    background: rgba(0,0,0,0.75);
}

.modal-box {
    background: #161b22;
    border: solid #30363d;
    padding: 2 4;
    width: 72;
    height: auto;
}

.modal-title {
    text-style: bold;
    color: #6ea8fe;
    margin-bottom: 1;
}

.field-label {
    color: #6e7681;
    margin-top: 1;
}

Input {
    background: #0d1117;
    border: solid #30363d;
    color: #e6edf3;
}

Input:focus {
    border: solid #388bfd;
}

Select {
    background: #0d1117;
    border: solid #30363d;
    color: #e6edf3;
}

.btn-row {
    layout: horizontal;
    height: auto;
    margin-top: 2;
    align: right middle;
}

Button {
    margin-left: 1;
    background: #21262d;
    color: #e6edf3;
    border: solid #30363d;
}

Button:hover {
    background: #30363d;
}

Button.primary {
    background: #1f6feb;
    color: #ffffff;
    border: solid #1f6feb;
}

Button.primary:hover {
    background: #388bfd;
    border: solid #388bfd;
}

Button.danger {
    background: #b91c1c;
    color: #ffffff;
    border: solid #b91c1c;
}

Button.danger:hover {
    background: #dc2626;
}

.error-label {
    color: #f85149;
    margin-top: 1;
}
"""


class ConnectModal(ModalScreen):
    DEFAULT_PORTS = {"FTP": "21", "FTPS": "21", "SFTP": "22", "SCP": "22"}

    def compose(self) -> ComposeResult:
        with Container(classes="modal-screen"):
            with Vertical(classes="modal-box"):
                yield Label("New Connection", classes="modal-title")
                yield Label("Protocol", classes="field-label")
                yield Select([(p, p) for p in ("SFTP", "SCP", "FTP", "FTPS")], value="SFTP", id="proto")
                yield Label("Host", classes="field-label")
                yield Input(placeholder="example.com", id="host")
                yield Label("Port", classes="field-label")
                yield Input(placeholder="22", value="22", id="port")
                yield Label("Username", classes="field-label")
                yield Input(placeholder="user", id="user")
                yield Label("Password", classes="field-label")
                yield Input(placeholder="leave blank to use SSH key", password=True, id="password")
                yield Label("SSH Key Path  (optional)", classes="field-label")
                yield Input(placeholder="~/.ssh/id_rsa", id="keypath")
                yield Label("Save as bookmark  (optional)", classes="field-label")
                yield Input(placeholder="my-server", id="bookmark-name")
                yield Label("", id="connect-error", classes="error-label")
                with Horizontal(classes="btn-row"):
                    yield Button("Cancel", id="cancel-btn")
                    yield Button("Connect", id="connect-btn", classes="primary")

    @on(Select.Changed, "#proto")
    def _proto_changed(self, event: Select.Changed):
        self.query_one("#port", Input).value = self.DEFAULT_PORTS.get(str(event.value), "22")

    @on(Button.Pressed, "#connect-btn")
    def _do_connect(self):
        self._attempt_connect()

    @on(Button.Pressed, "#cancel-btn")
    def _cancel(self):
        self.dismiss(None)

    def _attempt_connect(self):
        proto    = str(self.query_one("#proto", Select).value)
        host     = self.query_one("#host", Input).value.strip()
        port_str = self.query_one("#port", Input).value.strip()
        user     = self.query_one("#user", Input).value.strip()
        password = self.query_one("#password", Input).value or None
        keypath  = self.query_one("#keypath", Input).value.strip() or None
        bm_name  = self.query_one("#bookmark-name", Input).value.strip() or None
        err      = self.query_one("#connect-error", Label)

        if not host:
            err.update("Host is required")
            return
        if not user:
            err.update("Username is required")
            return
        try:
            port = int(port_str)
        except ValueError:
            err.update("Invalid port")
            return

        err.update("Connecting...")

        def _thread():
            try:
                client = connect(proto, host, port, user, password, keypath)
                if bm_name:
                    bm.save_bookmark(bm_name, proto, host, port, user, password, keypath)
                self.app.call_from_thread(self.dismiss, client)
            except Exception as e:
                self.app.call_from_thread(err.update, f"Error: {e}")

        threading.Thread(target=_thread, daemon=True).start()


class BookmarksModal(ModalScreen):
    def compose(self) -> ComposeResult:
        saved = bm.list_bookmarks()
        with Container(classes="modal-screen"):
            with Vertical(classes="modal-box"):
                yield Label("Bookmarks", classes="modal-title")
                if not saved:
                    yield Label("No bookmarks saved yet.", classes="field-label")
                else:
                    for b in saved:
                        yield Button(
                            f"{b['protocol']}  {b['user']}@{b['host']}:{b['port']}   [{b['name']}]",
                            id=f"bm-{b['name']}",
                        )
                with Horizontal(classes="btn-row"):
                    yield Button("Close", id="close-bm")

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed):
        bid = event.button.id or ""
        if bid == "close-bm":
            self.dismiss(None)
        elif bid.startswith("bm-"):
            entry = bm.get_bookmark(bid[3:])
            self.dismiss(entry)


class InputModal(ModalScreen):
    def __init__(self, title: str, label: str, default: str = ""):
        super().__init__()
        self._title   = title
        self._label   = label
        self._default = default

    def compose(self) -> ComposeResult:
        with Container(classes="modal-screen"):
            with Vertical(classes="modal-box"):
                yield Label(self._title, classes="modal-title")
                yield Label(self._label, classes="field-label")
                yield Input(value=self._default, id="val")
                with Horizontal(classes="btn-row"):
                    yield Button("Cancel", id="cancel")
                    yield Button("OK", id="ok", classes="primary")

    @on(Button.Pressed, "#ok")
    def _ok(self):
        self.dismiss(self.query_one("#val", Input).value)

    @on(Button.Pressed, "#cancel")
    def _cancel(self):
        self.dismiss(None)

    @on(Input.Submitted)
    def _submitted(self):
        self.dismiss(self.query_one("#val", Input).value)


class ConfirmModal(ModalScreen):
    def __init__(self, message: str):
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Container(classes="modal-screen"):
            with Vertical(classes="modal-box"):
                yield Label("Confirm", classes="modal-title")
                yield Label(self._message)
                with Horizontal(classes="btn-row"):
                    yield Button("Cancel", id="cancel")
                    yield Button("Delete", id="ok", classes="danger")

    @on(Button.Pressed, "#ok")
    def _ok(self):
        self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def _cancel(self):
        self.dismiss(False)


class FilePane(Vertical):
    current_path: reactive[str] = reactive("", layout=True)

    def __init__(self, title: str, is_local: bool, pane_id: str):
        super().__init__(classes="pane", id=pane_id)
        self._title   = title
        self.is_local = is_local
        self._entries: list[FileEntry] = []
        self._client  = None

    def compose(self) -> ComposeResult:
        yield Label(self._title, classes="pane-title", id=f"{self.id}-title")
        yield Label("", id=f"{self.id}-path", classes="pane-path")
        table = DataTable(id=f"{self.id}-table", cursor_type="row", zebra_stripes=True)
        table.add_columns("Name", "Size", "Modified", "Perms")
        yield table

    def on_mount(self):
        if self.is_local:
            self.navigate(str(Path.home()))

    def navigate(self, path: str):
        if self.is_local:
            self._load_local(path)
        elif self._client:
            self._load_remote(path)

    def _load_local(self, path: str):
        p = Path(path)
        if not p.is_dir():
            return
        entries: list[FileEntry] = []
        try:
            for item in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                try:
                    st = item.stat()
                    entries.append(FileEntry(name=item.name, size=st.st_size, is_dir=item.is_dir()))
                except PermissionError:
                    pass
        except PermissionError:
            return
        self.current_path = str(p)
        self._refresh_table(entries, str(p))

    def _load_remote(self, path: str):
        try:
            new_path = self._client.cd(path)
            entries  = self._client.ls(new_path)
            self.current_path = new_path
            self._refresh_table(entries, new_path)
        except Exception as e:
            self.app.notify(f"Error: {e}", severity="error")

    def _refresh_table(self, entries: list[FileEntry], path: str):
        self._entries = entries
        table = self.query_one(DataTable)
        table.clear()
        if path not in ("/", ""):
            table.add_row("[DIR] ..", "", "", "", key="__parent__")
        for e in entries:
            prefix = "[DIR]" if e.is_dir else "     "
            mod    = e.modified.strftime("%Y-%m-%d %H:%M") if e.modified else ""
            table.add_row(f"{prefix} {e.name}", e.size_human, mod, e.permissions, key=e.name)
        self.query_one(f"#{self.id}-path", Label).update(f" {path}")

    def get_selected_entry(self) -> Optional[FileEntry]:
        table = self.query_one(DataTable)
        try:
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            key_str = str(row_key.value)
            if key_str == "__parent__":
                return FileEntry(name="..", size=0, is_dir=True)
            for e in self._entries:
                if e.name == key_str:
                    return e
        except Exception:
            pass
        return None

    def enter_selected(self):
        entry = self.get_selected_entry()
        if not entry or not entry.is_dir:
            return
        if entry.name == "..":
            parent = str(Path(self.current_path).parent) if self.is_local else \
                     posixpath.dirname(self.current_path.rstrip("/")) or "/"
        else:
            parent = os.path.join(self.current_path, entry.name) if self.is_local else \
                     posixpath.join(self.current_path, entry.name)
        self.navigate(parent)

    def refresh_current(self):
        self.navigate(self.current_path)


class FtuiApp(App):
    TITLE = "ftui"
    CSS   = CSS

    BINDINGS = [
        Binding("f2",    "connect",        "Connect"),
        Binding("f3",    "bookmarks",       "Bookmarks"),
        Binding("f5",    "transfer",        "Transfer"),
        Binding("f7",    "mkdir",           "Mkdir"),
        Binding("f8",    "delete_selected", "Delete"),
        Binding("f9",    "rename_selected", "Rename"),
        Binding("tab",   "switch_pane",     "Switch pane"),
        Binding("enter", "enter_dir",       "Open"),
        Binding("q",     "quit",            "Quit"),
    ]

    _active_pane: str = "local"
    _client = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="layout"):
            yield FilePane("Local", is_local=True,  pane_id="local")
            yield FilePane("Remote  [not connected]", is_local=False, pane_id="remote")
        with Horizontal(id="transfer-bar"):
            yield Label("", id="transfer-label")
            yield ProgressBar(id="transfer-progress", total=100, show_eta=True)
        yield Static(
            "F2 Connect   F3 Bookmarks   F5 Transfer   F7 Mkdir   F8 Delete   F9 Rename   Tab Switch   Enter Open   Q Quit",
            classes="status-bar",
        )
        yield Footer()

    def on_mount(self):
        self.action_connect()

    def _local_pane(self)  -> FilePane: return self.query_one("#local",  FilePane)
    def _remote_pane(self) -> FilePane: return self.query_one("#remote", FilePane)
    def _active_file_pane(self) -> FilePane:
        return self._local_pane() if self._active_pane == "local" else self._remote_pane()

    def _sync_pane_from_focus(self):
        try:
            fid = getattr(self.focused, "id", None)
            if fid == "local-table":
                self._active_pane = "local"
            elif fid == "remote-table":
                self._active_pane = "remote"
        except Exception:
            pass

    def action_switch_pane(self):
        self._active_pane = "remote" if self._active_pane == "local" else "local"
        try:
            self.query_one(f"#{self._active_pane}-table", DataTable).focus()
        except NoMatches:
            pass

    def action_enter_dir(self):
        self._sync_pane_from_focus()
        self._active_file_pane().enter_selected()

    @on(DataTable.RowSelected)
    def _row_selected(self, event: DataTable.RowSelected):
        tid = event.data_table.id or ""
        if tid == "local-table":
            self._active_pane = "local"
        elif tid == "remote-table":
            self._active_pane = "remote"
        self._active_file_pane().enter_selected()

    def action_connect(self):
        def _handle(client):
            if client is None:
                return
            self._client = client
            remote = self._remote_pane()
            remote._client = client
            remote.query_one("#remote-title", Label).update(f"Remote  [{client.protocol}]")
            remote.navigate(client.cwd)
            self.notify(f"Connected via {client.protocol}", title="Connected")
        self.push_screen(ConnectModal(), _handle)

    def action_bookmarks(self):
        def _handle(entry):
            if entry is None:
                return
            def _thread():
                try:
                    client = connect(
                        entry["protocol"], entry["host"], entry["port"],
                        entry["user"], entry.get("password"), entry.get("key_path"),
                    )
                    self.app.call_from_thread(_apply, client)
                except Exception as e:
                    self.app.call_from_thread(self.notify, str(e), severity="error")
            def _apply(client):
                self._client = client
                remote = self._remote_pane()
                remote._client = client
                remote.query_one("#remote-title", Label).update(f"Remote  [{client.protocol}]")
                remote.navigate(entry.get("remote_path", "/"))
                self.notify(f"Connected to {entry['host']}", title="Bookmark")
            threading.Thread(target=_thread, daemon=True).start()
        self.push_screen(BookmarksModal(), _handle)

    def action_transfer(self):
        if not self._client:
            self.notify("Not connected.", severity="warning")
            return
        self._sync_pane_from_focus()
        active = self._active_file_pane()
        entry  = active.get_selected_entry()
        if not entry or entry.name == "..":
            self.notify("Select a file or directory.", severity="warning")
            return
        if active.is_local:
            local_path  = os.path.join(active.current_path, entry.name)
            remote_path = posixpath.join(self._remote_pane().current_path, entry.name)
            if entry.is_dir:
                self._run_dir_transfer("upload", local_path, remote_path, entry.name)
            else:
                self._run_transfer("upload", local_path, remote_path, entry.name)
        else:
            remote_path = posixpath.join(active.current_path, entry.name)
            local_path  = os.path.join(self._local_pane().current_path, entry.name)
            if entry.is_dir:
                self._run_dir_transfer("download", local_path, remote_path, entry.name)
            else:
                self._run_transfer("download", local_path, remote_path, entry.name)

    def _run_transfer(self, direction: str, local: str, remote: str, name: str):
        bar      = self.query_one("#transfer-bar")
        label    = self.query_one("#transfer-label", Label)
        progress = self.query_one("#transfer-progress", ProgressBar)
        bar.add_class("visible")
        label.update(f" {'UP' if direction == 'upload' else 'DN'}  {name} ")

        def _cb(transferred: int, total: int):
            if total > 0:
                self.call_from_thread(progress.update, progress=int(transferred / total * 100))

        def _run():
            try:
                if direction == "upload":
                    self._client.upload(local, remote, _cb)
                else:
                    self._client.download(remote, local, _cb)
                self.call_from_thread(self._transfer_done, name, direction)
            except Exception as e:
                self.call_from_thread(self.notify, f"Transfer failed: {e}", severity="error")
                self.call_from_thread(bar.remove_class, "visible")

        threading.Thread(target=_run, daemon=True).start()

    def _run_dir_transfer(self, direction: str, local: str, remote: str, name: str):
        bar      = self.query_one("#transfer-bar")
        label    = self.query_one("#transfer-label", Label)
        progress = self.query_one("#transfer-progress", ProgressBar)
        bar.add_class("visible")
        arrow    = "UP" if direction == "upload" else "DN"
        counters = {"done": 0, "total": 0}

        def _file_cb(transferred: int, total: int):
            if transferred == total and total > 0:
                counters["done"] += 1
            if counters["total"] > 0:
                pct = int(counters["done"] / counters["total"] * 100)
                self.call_from_thread(progress.update, progress=pct)
                self.call_from_thread(
                    label.update,
                    f" {arrow}  {name}/  [{counters['done']}/{counters['total']} files] "
                )

        def _run():
            try:
                if direction == "upload":
                    counters["total"] = sum(1 for p in Path(local).rglob("*") if p.is_file())
                    self.call_from_thread(label.update, f" {arrow}  {name}/  [0/{counters['total']} files] ")
                    self._client.upload_dir(local, remote, _file_cb)
                else:
                    self.call_from_thread(label.update, f" {arrow}  {name}/  [downloading...] ")
                    self._client.download_dir(remote, local, _file_cb)
                self.call_from_thread(self._transfer_done, name, direction)
            except Exception as e:
                self.call_from_thread(self.notify, f"Transfer failed: {e}", severity="error")
                self.call_from_thread(bar.remove_class, "visible")

        threading.Thread(target=_run, daemon=True).start()

    def _transfer_done(self, name: str, direction: str):
        self.query_one("#transfer-bar").remove_class("visible")
        self.notify(f"{'Uploaded' if direction == 'upload' else 'Downloaded'}: {name}", title="Transfer complete")
        self._local_pane().refresh_current()
        self._remote_pane().refresh_current()

    def action_mkdir(self):
        self._sync_pane_from_focus()
        pane = self._active_file_pane()

        def _handle(name):
            if not name:
                return
            try:
                if pane.is_local:
                    Path(pane.current_path, name).mkdir()
                else:
                    self._client.mkdir(posixpath.join(pane.current_path, name))
                pane.refresh_current()
                self.notify(f"Created: {name}")
            except Exception as e:
                self.notify(str(e), severity="error")

        self.push_screen(InputModal("New Directory", "Directory name:"), _handle)

    def action_delete_selected(self):
        self._sync_pane_from_focus()
        pane  = self._active_file_pane()
        entry = pane.get_selected_entry()
        if not entry or entry.name == "..":
            return

        def _handle(confirmed):
            if not confirmed:
                return
            try:
                if pane.is_local:
                    target = Path(pane.current_path) / entry.name
                    if entry.is_dir:
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                else:
                    path = posixpath.join(pane.current_path, entry.name)
                    if entry.is_dir:
                        self._delete_remote_dir(path)
                    else:
                        self._client.delete(path, is_dir=False)
                pane.refresh_current()
                self.notify(f"Deleted: {entry.name}")
            except Exception as e:
                self.notify(f"Delete failed: {e}", severity="error")

        self.push_screen(ConfirmModal(f"Delete '{entry.name}'?"), _handle)

    def _delete_remote_dir(self, path: str):
        entries = self._client.ls(path)
        for e in entries:
            child = posixpath.join(path, e.name)
            if e.is_dir:
                self._delete_remote_dir(child)
            else:
                self._client.delete(child, is_dir=False)
        self._client.delete(path, is_dir=True)

    def action_rename_selected(self):
        self._sync_pane_from_focus()
        pane  = self._active_file_pane()
        entry = pane.get_selected_entry()
        if not entry or entry.name == "..":
            return

        def _handle(new_name):
            if not new_name or new_name == entry.name:
                return
            try:
                if pane.is_local:
                    Path(pane.current_path, entry.name).rename(Path(pane.current_path, new_name))
                else:
                    old = posixpath.join(pane.current_path, entry.name)
                    new = posixpath.join(pane.current_path, new_name)
                    self._client.rename(old, new)
                pane.refresh_current()
                self.notify(f"Renamed to: {new_name}")
            except Exception as e:
                self.notify(f"Rename failed: {e}", severity="error")

        self.push_screen(InputModal("Rename", "New name:", entry.name), _handle)


def main():
    app = FtuiApp()
    app.run()


if __name__ == "__main__":
    main()
