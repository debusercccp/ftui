"""ftui - dual-pane TUI file transfer client
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
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.widgets import DataTable, Header, Label, ProgressBar, Static

from ftui.styles import CSS
from ftui.protocols import FileEntry, connect
from ftui.modals import ConnectModal, BookmarksModal, InputModal, ConfirmModal
from ftui.pane import FilePane
from ftui.nas_sync import NasSyncModal
from ftui import bookmarks as bm


class FtuiApp(App):
    TITLE = "ftui"
    CSS   = CSS

    BINDINGS = [
        Binding("f2",    "connect",         "Connect"),
        Binding("f3",    "bookmarks",        "Bookmarks"),
        Binding("f5",    "transfer",         "Transfer"),
        Binding("f6",    "nas_sync",         "NAS Sync"),
        Binding("f7",    "mkdir",            "Mkdir"),
        Binding("f8",    "delete_selected",  "Delete"),
        Binding("f9",    "rename_selected",  "Rename"),
        Binding("tab",   "switch_pane",      "Switch pane"),
        Binding("enter", "enter_dir",        "Open"),
        Binding("q",     "quit",             "Quit"),
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
            "F2 Connect   F3 Bookmarks   F5 Transfer   F6 NAS Sync   "
            "F7 Mkdir   F8 Delete   F9 Rename   Tab Switch   Enter Open   Q Quit",
            classes="status-bar",
        )

    def on_mount(self):
        self.action_connect()

    # ── pane helpers ──────────────────────────────────────────────────────

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

    # ── navigation ────────────────────────────────────────────────────────

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

    # ── connect / bookmarks ───────────────────────────────────────────────

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

    # ── NAS sync ──────────────────────────────────────────────────────────

    def action_nas_sync(self):
        self.push_screen(NasSyncModal(self._client))

    # ── transfer ──────────────────────────────────────────────────────────

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
            direction   = "upload"
        else:
            remote_path = posixpath.join(active.current_path, entry.name)
            local_path  = os.path.join(self._local_pane().current_path, entry.name)
            direction   = "download"

        if entry.is_dir:
            self._run_dir_transfer(direction, local_path, remote_path, entry.name)
        else:
            self._run_transfer(direction, local_path, remote_path, entry.name)

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
        self.notify(
            f"{'Uploaded' if direction == 'upload' else 'Downloaded'}: {name}",
            title="Transfer complete",
        )
        self._local_pane().refresh_current()
        self._remote_pane().refresh_current()

    # ── mkdir / delete / rename ───────────────────────────────────────────

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
                    shutil.rmtree(target) if entry.is_dir else target.unlink()
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
        try:
            entries = self._client.ls(path)
        except Exception as e:
            self.notify(f"Cannot list {path}: {e}", severity="error")
            return
        for e in entries:
            child = posixpath.join(path, e.name)
            if e.is_dir:
                self._delete_remote_dir(child)
            else:
                try:
                    self._client.delete(child, is_dir=False)
                except Exception as ex:
                    self.notify(f"Cannot delete {child}: {ex}", severity="error")
                    return
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
    FtuiApp().run()


if __name__ == "__main__":
    main()
