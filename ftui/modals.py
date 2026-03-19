"""ftui/modals.py — Modal screens: Connect, Bookmarks, Input, Confirm."""
from __future__ import annotations

import threading

from textual import on
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select

from ftui.protocols import connect
from ftui import bookmarks as bm


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
                    yield Button("Cancel",  id="cancel-btn")
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
            err.update("Host is required"); return
        if not user:
            err.update("Username is required"); return
        try:
            port = int(port_str)
        except ValueError:
            err.update("Invalid port"); return

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
                    yield Button("OK",     id="ok", classes="primary")

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
