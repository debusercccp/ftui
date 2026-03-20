"""ftui/nas_sync.py — NAS Sync modal per ftui (F6)."""
from __future__ import annotations

import logging
import posixpath
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

logging.basicConfig(
    filename="/tmp/nas_sync_debug.log",
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("nas_sync")

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from ftui.protocols import FTPClient, FileEntry

CONFLICT_THRESHOLD = 60


class ConflictModal(ModalScreen):
    def __init__(self, rel_path: str, local_ts: int, remote_ts: int):
        super().__init__()
        self._rel    = rel_path
        self._local  = datetime.fromtimestamp(local_ts).strftime("%Y-%m-%d %H:%M:%S")
        self._remote = datetime.fromtimestamp(remote_ts).strftime("%Y-%m-%d %H:%M:%S")

    def compose(self) -> ComposeResult:
        with Container(classes="modal-screen"):
            with Vertical(classes="modal-box"):
                yield Label("⚠ Conflitto", classes="modal-title")
                yield Label(f"File: {self._rel}")
                yield Label(f"  Locale : {self._local}", classes="field-label")
                yield Label(f"  NAS    : {self._remote}", classes="field-label")
                with Horizontal(classes="btn-row"):
                    yield Button("Locale vince", id="local")
                    yield Button("NAS vince",    id="nas",  classes="primary")
                    yield Button("Salta",         id="skip", classes="danger")

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id or "skip")


class NasSyncModal(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss_modal", "Chiudi")]

    def __init__(self, existing_client=None):
        super().__init__()
        self._client: Optional[FTPClient] = (
            existing_client if isinstance(existing_client, FTPClient) else None
        )
        self._running  = False
        log.debug(f"NasSyncModal __init__, id={id(self)}")
        self._log_msgs: list[tuple[str, str]] = []


    def compose(self) -> ComposeResult:
        with Container(classes="modal-screen"):
            with Vertical(classes="modal-box", id="sync-box"):
                yield Label("NAS Sync  (bidirezionale)", classes="modal-title")

                if not self._client:
                    yield Label("Host NAS", classes="field-label")
                    yield Input(placeholder="192.168.1.156", id="sync-host")
                    yield Label("Porta", classes="field-label")
                    yield Input(value="21", id="sync-port")
                    yield Label("Utente", classes="field-label")
                    yield Input(placeholder="rocco", id="sync-user")
                    yield Label("Password", classes="field-label")
                    yield Input(password=True, id="sync-pass")

                yield Label("Cartelle NAS (separate da virgola)", classes="field-label")
                yield Input(
                    placeholder="Documenti, corsoML, github, myconfig, neural-lib",
                    id="sync-dirs",
                )
                yield Label("Destinazione locale", classes="field-label")
                yield Input(value=str(Path.home()), id="sync-local")

                yield Label("", id="sync-status", classes="field-label")
                with ScrollableContainer(id="sync-log"):
                    yield Static("", id="sync-log-content")

                with Horizontal(classes="btn-row"):
                    yield Button("Chiudi",  id="sync-close")
                    yield Button("▶ Avvia", id="sync-start", classes="primary")

    def _log(self, msg: str, kind: str = "info"):
        log.debug(f"[{kind}] {msg}")
        ts   = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self._log_msgs.append((line, f"sync-{kind}"))
        markup = "\n".join(
            f"[{cls}]{txt}[/{cls}]" for txt, cls in self._log_msgs[-120:]
        )
        try:
            self.query_one("#sync-log-content", Static).update(markup)
        except Exception as e:
            log.debug(f"_log update fallito: {e}")

    def _set_status(self, msg: str):
        log.debug(f"status: {msg}")
        try:
            self.query_one("#sync-status", Label).update(msg)
        except Exception as e:
            log.debug(f"_set_status fallito: {e}")

    def _get_input(self, wid: str, default: str = "") -> str:
        try:
            return self.query_one(f"#{wid}", Input).value.strip() or default
        except Exception:
            return default

    @on(Button.Pressed, "#sync-close")
    def _close(self):
        self.dismiss(None)

    @on(Button.Pressed, "#sync-start")
    def _start(self):
        log.debug(f"_start premuto, _running={self._running}")
        if self._running:
            return
        self._running = True
        self._log_msgs.clear()
        self.query_one("#sync-start", Button).disabled = True
        self._set_status("Connessione in corso...")
        threading.Thread(target=self._run_sync, daemon=True).start()

    def action_dismiss_modal(self):
        self.dismiss(None)

    def _run_sync(self):
        log.debug("_run_sync avviato")
        try:
            client = self._get_or_connect()
            log.debug(f"client ok: {client}")
        except Exception as e:
            log.debug(f"connessione fallita: {e}")
            self.app.call_from_thread(self._log, f"Connessione fallita: {e}", "err")
            self.app.call_from_thread(self._set_status, "Errore connessione")
            self.app.call_from_thread(self._finish)
            return

        dirs_raw   = self._get_input("sync-dirs")
        local_base = Path(self._get_input("sync-local", str(Path.home())))
        sync_dirs  = [d.strip() for d in dirs_raw.split(",") if d.strip()]
        log.debug(f"dirs={sync_dirs} local_base={local_base}")

        if not sync_dirs:
            self.app.call_from_thread(self._log, "Nessuna cartella specificata.", "warn")
            self.app.call_from_thread(self._finish)
            return

        self.app.call_from_thread(self._log, f"Connesso. Sync: {', '.join(sync_dirs)}", "ok")

        total_dl = total_ul = 0
        for d in sync_dirs:
            dl, ul = self._sync_dir(client, d, local_base)
            total_dl += dl
            total_ul += ul

        self.app.call_from_thread(
            self._set_status,
            f"Completato: {total_dl} scaricati  {total_ul} caricati"
        )
        self.app.call_from_thread(self._finish)

    def _get_or_connect(self) -> FTPClient:
        log.debug("_get_or_connect")
        if self._client:
            log.debug("riuso client esistente")
            return self._client
        host = self._get_input("sync-host")
        port = int(self._get_input("sync-port", "21"))
        user = self._get_input("sync-user")
        pw   = self._get_input("sync-pass")
        log.debug(f"nuova connessione {host}:{port} utente={user}")
        return FTPClient(host, port, user, pw, tls=False)

    def _sync_dir(self, client: FTPClient, rel_dir: str, local_base: Path) -> tuple[int, int]:
        self.app.call_from_thread(self._log, f"-- {rel_dir} --", "info")
        log.debug(f"_sync_dir: {rel_dir}")
        try:
            remote_entries = client.ls(rel_dir)
            log.debug(f"ls {rel_dir}: {len(remote_entries)} entry")
        except Exception as e:
            log.debug(f"ls fallito su {rel_dir}: {e}")
            self.app.call_from_thread(self._log, f"  Errore ls {rel_dir}: {e}", "err")
            return 0, 0
        return self._process_entries(client, remote_entries, rel_dir, local_base / rel_dir)

    def _process_entries(
        self,
        client: FTPClient,
        remote_entries: list[FileEntry],
        remote_dir: str,
        local_dir: Path,
    ) -> tuple[int, int]:
        downloaded = uploaded = 0
        local_dir.mkdir(parents=True, exist_ok=True)
        remote_map: dict[str, FileEntry] = {e.name: e for e in remote_entries}

        for entry in remote_entries:
            remote_path = posixpath.join(remote_dir, entry.name)
            local_path  = local_dir / entry.name

            if entry.is_dir:
                try:
                    sub = client.ls(remote_path)
                    dl, ul = self._process_entries(client, sub, remote_path, local_path)
                    downloaded += dl
                    uploaded   += ul
                except Exception as e:
                    self.app.call_from_thread(self._log, f"  Errore in {remote_path}: {e}", "err")
                continue

            if not local_path.exists():
                self.app.call_from_thread(self._log, f"  down {entry.name}", "info")
                self._download(client, remote_path, local_path)
                downloaded += 1
                continue

            local_ts  = int(local_path.stat().st_mtime)
            remote_ts = self._get_remote_mtime(client, remote_path, entry)

            if remote_ts == 0:
                self.app.call_from_thread(self._log, f"  ? data sconosciuta, salto {entry.name}", "warn")
                continue

            delta = remote_ts - local_ts
            if abs(delta) <= CONFLICT_THRESHOLD:
                self.app.call_from_thread(self._log, f"  = {entry.name}", "ok")
                continue

            choice = self._ask_conflict(entry.name, remote_path, local_path, local_ts, remote_ts)
            if choice == "nas":
                self.app.call_from_thread(self._log, f"  down NAS vince: {entry.name}", "info")
                self._download(client, remote_path, local_path)
                downloaded += 1
            elif choice == "local":
                self.app.call_from_thread(self._log, f"  up Locale vince: {entry.name}", "info")
                self._upload(client, local_path, remote_path)
                uploaded += 1
            else:
                self.app.call_from_thread(self._log, f"  ~ saltato: {entry.name}", "warn")

        try:
            local_items = list(local_dir.iterdir())
        except Exception:
            local_items = []

        for local_path in local_items:
            if local_path.name in remote_map:
                continue
            remote_path = posixpath.join(remote_dir, local_path.name)
            if local_path.is_dir():
                try:
                    client.mkdir(remote_path)
                except Exception:
                    pass
                dl, ul = self._process_entries(client, [], remote_path, local_path)
                downloaded += dl
                uploaded   += ul
                continue
            self.app.call_from_thread(self._log, f"  up {local_path.name}", "info")
            self._upload(client, local_path, remote_path)
            uploaded += 1

        return downloaded, uploaded

    def _get_remote_mtime(self, client: FTPClient, remote_path: str, entry: FileEntry) -> int:
        try:
            resp = client._ftp.voidcmd(f"MDTM {remote_path}")
        except Exception as e:
            log.debug(f"MDTM fallito {remote_path}: {e}")
            resp = ""

        if resp.startswith("213 "):
            raw = resp[4:].strip()
            if len(raw) >= 14:
                try:
                    ts = int(datetime.strptime(raw[:14], "%Y%m%d%H%M%S").timestamp())
                    log.debug(f"MDTM {remote_path}: raw={raw} ts={ts} ({datetime.fromtimestamp(ts)})")
                    return ts
                except Exception as e:
                    log.debug(f"MDTM parse fallito: {e}")

        if entry.modified:
            ts = int(entry.modified.timestamp())
            log.debug(f"entry.modified {remote_path}: ts={ts} ({entry.modified})")
            return ts

        log.debug(f"nessun timestamp per {remote_path}")
        return 0

    def _download(self, client: FTPClient, remote: str, local: Path):
        try:
            client.download(remote, str(local))
            log.debug(f"scaricato: {remote} -> {local}")
        except Exception as e:
            log.debug(f"download fallito {remote}: {e}")
            self.app.call_from_thread(self._log, f"  errore download {local.name}: {e}", "err")

    def _upload(self, client: FTPClient, local: Path, remote: str):
        try:
            client.upload(str(local), remote)
            log.debug(f"caricato: {local} -> {remote}")
        except Exception as e:
            log.debug(f"upload fallito {remote}: {e}")
            self.app.call_from_thread(self._log, f"  errore upload {local.name}: {e}", "err")

    def _ask_conflict(
        self,
        name: str,
        remote_path: str,
        local_path: Path,
        local_ts: int,
        remote_ts: int,
    ) -> str:
        result_holder: list[str] = []
        event = threading.Event()

        def _show():
            def _handle(choice: str):
                result_holder.append(choice or "skip")
                event.set()
            try:
                self.app.push_screen(ConflictModal(name, local_ts, remote_ts), _handle)
            except Exception as e:
                log.debug(f"push_screen fallito: {e}")
                result_holder.append("skip")
                event.set()

        self.app.call_from_thread(_show)
        fired = event.wait(timeout=120)
        if not fired:
            log.debug(f"timeout conflitto per {name}, salto")
            return "skip"
        return result_holder[0] if result_holder else "skip"

    def _finish(self):
        log.debug("_finish chiamato")
        self._running = False
        try:
            self.query_one("#sync-start", Button).disabled = False
        except Exception:
            pass
