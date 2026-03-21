"""ftui/pane.py — FilePane: dual-pane widget locale/remoto."""
from __future__ import annotations

import os
import posixpath
from pathlib import Path
from typing import Optional

from textual.reactive import reactive
from textual.containers import Vertical
from textual.widgets import DataTable, Label

from ftui.protocols import FileEntry


def _sanitize(s: str) -> str:
    """Converte una stringa in UTF-8 valido, sostituendo caratteri problematici."""
    try:
        return s.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    except Exception:
        return s


class FilePane(Vertical):
    current_path: reactive[str] = reactive("", layout=True)

    def __init__(self, title: str, is_local: bool, pane_id: str):
        super().__init__(classes="pane", id=pane_id)
        self._title   = title
        self.is_local = is_local
        self._entries: list[FileEntry] = []
        self._client  = None

    def compose(self):
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
            prefix   = "[DIR]" if e.is_dir else "     "
            mod      = e.modified.strftime("%Y-%m-%d %H:%M") if e.modified else ""
            # sanitize del nome per evitare crash UTF-8 con caratteri non-ASCII
            disp_name = _sanitize(e.name)
            table.add_row(
                f"{prefix} {disp_name}",
                e.size_human,
                mod,
                e.permissions,
                key=e.name,   # key rimane il nome originale per la navigazione
            )
        self.query_one(f"#{self.id}-path", Label).update(f" {_sanitize(path)}")

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
            parent = (
                str(Path(self.current_path).parent) if self.is_local
                else posixpath.dirname(self.current_path.rstrip("/")) or "/"
            )
        else:
            parent = (
                os.path.join(self.current_path, entry.name) if self.is_local
                else posixpath.join(self.current_path, entry.name)
            )
        self.navigate(parent)

    def refresh_current(self):
        self.navigate(self.current_path)
