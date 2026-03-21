"""ftui/nas_sync.py — NAS Sync modal per ftui (F6).

Funzionalità:
- Sync bidirezionale FTP ↔ locale
- Worker asincrono Textual (niente threading.Event)
- Esclusioni configurabili da ~/.config/ftui/sync_exclude.txt
- Confronto size + timestamp per ridurre falsi conflitti
- Stato persistente in ~/.config/ftui/sync_state.json
- Conflict modal con "applica a tutti"
"""
from __future__ import annotations

import fnmatch
import json
import logging
import posixpath
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("nas_sync")
logging.basicConfig(
    filename="/tmp/nas_sync_debug.log",
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
)

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

from ftui.protocols import FTPClient, FileEntry

# ── Costanti ──────────────────────────────────────────────────────────────────

CONFLICT_THRESHOLD = 60        # secondi: delta entro cui i file sono "uguali"
SIZE_THRESHOLD     = 0         # byte: delta size accettabile (0 = esatti)
CONFIG_DIR         = Path.home() / ".config" / "ftui"
STATE_FILE         = CONFIG_DIR / "sync_state.json"
EXCLUDE_FILE       = CONFIG_DIR / "sync_exclude.txt"

DEFAULT_EXCLUDES = [
    ".git", ".gitignore", ".gitmodules", ".gitattributes",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "*.pyc", "*.pyo", "*.egg-info", "dist", "build",
    ".venv", "venv", "env",
    "node_modules", ".npm", "*.lock",
    "target",
    ".DS_Store", "Thumbs.db", ".idea", ".vscode",
    "*.swp", "*.swo", "*.log", "*.tmp", "*.temp",
]


# ── Esclusioni ────────────────────────────────────────────────────────────────

def load_excludes() -> list[str]:
    """Carica pattern da file config, crea il file con i default se non esiste."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not EXCLUDE_FILE.exists():
        EXCLUDE_FILE.write_text("\n".join(DEFAULT_EXCLUDES) + "\n")
    lines = EXCLUDE_FILE.read_text().splitlines()
    return [l.strip() for l in lines if l.strip() and not l.startswith("#")]


def is_excluded(name: str, patterns: list[str]) -> bool:
    if name in patterns:
        return True
    return any(fnmatch.fnmatch(name, p) for p in patterns)


# ── Stato persistente ─────────────────────────────────────────────────────────

def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── Modal conflitto ───────────────────────────────────────────────────────────

class ConflictModal(ModalScreen):
    """Mostra i dettagli del conflitto e chiede come risolverlo."""

    def __init__(
        self,
        name: str,
        local_ts: int,
        remote_ts: int,
        local_size: int,
        remote_size: int,
    ):
        super().__init__()
        self._name        = name
        self._local_ts    = datetime.fromtimestamp(local_ts).strftime("%Y-%m-%d %H:%M:%S")
        self._remote_ts   = datetime.fromtimestamp(remote_ts).strftime("%Y-%m-%d %H:%M:%S")
        self._local_size  = self._fmt_size(local_size)
        self._remote_size = self._fmt_size(remote_size)

    @staticmethod
    def _fmt_size(b: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if b < 1024:
                return f"{b:.1f} {unit}"
            b /= 1024
        return f"{b:.1f} TB"

    def compose(self) -> ComposeResult:
        with Container(classes="modal-screen"):
            with Vertical(classes="modal-box"):
                yield Label("⚠ Conflitto", classes="modal-title")
                yield Label(f"File: {self._name}")
                yield Label(
                    f"  Locale : {self._local_ts}  ({self._local_size})",
                    classes="field-label",
                )
                yield Label(
                    f"  NAS    : {self._remote_ts}  ({self._remote_size})",
                    classes="field-label",
                )
                yield Label("Applica a tutti i conflitti successivi?", classes="field-label")
                yield Select(
                    [("Chiedi ogni volta", "ask"), ("NAS vince sempre", "nas_all"), ("Locale vince sempre", "local_all")],
                    value="ask",
                    id="apply-all",
                )
                with Horizontal(classes="btn-row"):
                    yield Button("Locale vince", id="local")
                    yield Button("NAS vince",    id="nas",  classes="primary")
                    yield Button("Salta",         id="skip", classes="danger")

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed):
        apply_all = str(self.query_one("#apply-all", Select).value)
        self.dismiss((event.button.id or "skip", apply_all))


# ── Modal principale ──────────────────────────────────────────────────────────

class NasSyncModal(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss_modal", "Chiudi")]

    def __init__(self, existing_client=None):
        super().__init__()
        self._client: Optional[FTPClient] = (
            existing_client if isinstance(existing_client, FTPClient) else None
        )
        self._log_msgs: list[tuple[str, str]] = []
        self._bulk_choice: Optional[str] = None   # "nas_all" | "local_all" | None
        self._excludes: list[str] = []
        self._state: dict = {}

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

    # ── helpers UI ────────────────────────────────────────────────────────

    def _log(self, msg: str, kind: str = "info"):
        log.debug(f"[{kind}] {msg}")
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_msgs.append((f"[{ts}] {msg}", f"sync-{kind}"))
        markup = "\n".join(
            f"[{cls}]{txt}[/{cls}]" for txt, cls in self._log_msgs[-200:]
        )
        try:
            self.query_one("#sync-log-content", Static).update(markup)
        except Exception:
            pass

    def _set_status(self, msg: str):
        try:
            self.query_one("#sync-status", Label).update(msg)
        except Exception:
            pass

    def _get_input(self, wid: str, default: str = "") -> str:
        try:
            return self.query_one(f"#{wid}", Input).value.strip() or default
        except Exception:
            return default

    def _set_running(self, running: bool):
        try:
            self.query_one("#sync-start", Button).disabled = running
        except Exception:
            pass

    # ── eventi ───────────────────────────────────────────────────────────

    @on(Button.Pressed, "#sync-close")
    def _close(self):
        self.dismiss(None)

    @on(Button.Pressed, "#sync-start")
    def _start(self):
        self._log_msgs.clear()
        self._bulk_choice = None
        self._excludes    = load_excludes()
        self._state       = load_state()
        self._set_running(True)
        self._set_status("Avvio sync...")
        self._run_sync()

    def action_dismiss_modal(self):
        self.dismiss(None)

    # ── worker asincrono ──────────────────────────────────────────────────

    @work(exclusive=True)
    async def _run_sync(self):
        try:
            client = self._get_or_connect()
        except Exception as e:
            self._log(f"Connessione fallita: {e}", "err")
            self._set_status("❌ Errore connessione")
            self._set_running(False)
            return

        dirs_raw   = self._get_input("sync-dirs")
        local_base = Path(self._get_input("sync-local", str(Path.home())))
        sync_dirs  = [d.strip() for d in dirs_raw.split(",") if d.strip()]

        if not sync_dirs:
            self._log("Nessuna cartella specificata.", "warn")
            self._set_running(False)
            return

        self._log(f"Sync: {', '.join(sync_dirs)}", "ok")
        total_dl = total_ul = 0

        for d in sync_dirs:
            dl, ul = await self._sync_dir(client, d, local_base)
            total_dl += dl
            total_ul += ul

        save_state(self._state)
        self._set_status(f"✔ Completato — ↓{total_dl} scaricati  ↑{total_ul} caricati")
        self._set_running(False)

    def _get_or_connect(self) -> FTPClient:
        if self._client:
            return self._client
        host = self._get_input("sync-host")
        port = int(self._get_input("sync-port", "21"))
        user = self._get_input("sync-user")
        pw   = self._get_input("sync-pass")
        log.debug(f"connessione {host}:{port} utente={user}")
        return FTPClient(host, port, user, pw, tls=False)

    async def _sync_dir(
        self, client: FTPClient, rel_dir: str, local_base: Path
    ) -> tuple[int, int]:
        self._log(f"── {rel_dir} ──", "info")
        try:
            remote_entries = client.ls(rel_dir)
        except Exception as e:
            self._log(f"  Errore ls {rel_dir}: {e}", "err")
            return 0, 0
        return await self._process_entries(
            client, remote_entries, rel_dir, local_base / rel_dir
        )

    async def _process_entries(
        self,
        client: FTPClient,
        remote_entries: list[FileEntry],
        remote_dir: str,
        local_dir: Path,
    ) -> tuple[int, int]:
        downloaded = uploaded = 0
        local_dir.mkdir(parents=True, exist_ok=True)
        remote_map: dict[str, FileEntry] = {e.name: e for e in remote_entries}

        # ── remoto → locale ───────────────────────────────────────────────
        for entry in remote_entries:
            if is_excluded(entry.name, self._excludes):
                log.debug(f"escluso: {entry.name}")
                continue

            remote_path = posixpath.join(remote_dir, entry.name)
            local_path  = local_dir / entry.name

            if entry.is_dir:
                try:
                    sub = client.ls(remote_path)
                    dl, ul = await self._process_entries(client, sub, remote_path, local_path)
                    downloaded += dl
                    uploaded   += ul
                except Exception as e:
                    self._log(f"  Errore in {remote_path}: {e}", "err")
                continue

            if not local_path.exists():
                self._log(f"  ↓ {entry.name}", "info")
                self._download(client, remote_path, local_path)
                self._state[str(local_path)] = int(datetime.now().timestamp())
                downloaded += 1
                continue

            local_ts    = int(local_path.stat().st_mtime)
            local_size  = local_path.stat().st_size
            remote_ts   = self._get_remote_mtime(client, remote_path, entry)
            remote_size = entry.size

            # Stesso contenuto (size + timestamp) → skip
            if (
                abs(local_size - remote_size) <= SIZE_THRESHOLD
                and remote_ts != 0
                and abs(remote_ts - local_ts) <= CONFLICT_THRESHOLD
            ):
                self._log(f"  = {entry.name}", "ok")
                continue

            if remote_ts == 0:
                # Nessun timestamp remoto: confronta solo size
                if abs(local_size - remote_size) <= SIZE_THRESHOLD:
                    self._log(f"  = {entry.name} (size)", "ok")
                else:
                    self._log(f"  ? timestamp sconosciuto, salto {entry.name}", "warn")
                continue

            delta = remote_ts - local_ts

            # NAS più vecchio del locale → upload silenzioso
            if delta < -CONFLICT_THRESHOLD:
                self._log(f"  ↑ locale recente: {entry.name}", "info")
                self._upload(client, local_path, remote_path)
                uploaded += 1
                continue

            # NAS più recente → chiedi (o applica bulk)
            if delta > CONFLICT_THRESHOLD:
                choice = await self._resolve_conflict(
                    entry.name, remote_path, local_path,
                    local_ts, remote_ts, local_size, remote_size,
                )
                if choice == "nas":
                    self._log(f"  ↓ NAS vince: {entry.name}", "info")
                    self._download(client, remote_path, local_path)
                    downloaded += 1
                elif choice == "local":
                    self._log(f"  ↑ Locale vince: {entry.name}", "info")
                    self._upload(client, local_path, remote_path)
                    uploaded += 1
                else:
                    self._log(f"  ~ saltato: {entry.name}", "warn")

        # ── solo locale → upload ──────────────────────────────────────────
        try:
            local_items = list(local_dir.iterdir())
        except Exception:
            local_items = []

        for local_path in local_items:
            if is_excluded(local_path.name, self._excludes):
                continue
            if local_path.name in remote_map:
                continue

            remote_path = posixpath.join(remote_dir, local_path.name)

            if local_path.is_dir():
                try:
                    client.mkdir(remote_path)
                except Exception:
                    pass
                dl, ul = await self._process_entries(client, [], remote_path, local_path)
                downloaded += dl
                uploaded   += ul
                continue

            self._log(f"  ↑ {local_path.name}", "info")
            self._upload(client, local_path, remote_path)
            uploaded += 1

        return downloaded, uploaded

    # ── risoluzione conflitti ─────────────────────────────────────────────

    async def _resolve_conflict(
        self,
        name: str,
        remote_path: str,
        local_path: Path,
        local_ts: int,
        remote_ts: int,
        local_size: int,
        remote_size: int,
    ) -> str:
        # Applica scelta bulk se già impostata
        if self._bulk_choice == "nas_all":
            return "nas"
        if self._bulk_choice == "local_all":
            return "local"

        # Chiedi all'utente
        result = await self.app.push_screen_wait(
            ConflictModal(name, local_ts, remote_ts, local_size, remote_size)
        )

        if result is None:
            return "skip"

        choice, apply_all = result

        if apply_all == "nas_all":
            self._bulk_choice = "nas_all"
        elif apply_all == "local_all":
            self._bulk_choice = "local_all"

        return choice or "skip"

    # ── I/O ──────────────────────────────────────────────────────────────

    def _get_remote_mtime(self, client: FTPClient, remote_path: str, entry: FileEntry) -> int:
        try:
            resp = client._ftp.sendcmd(f"MDTM {remote_path}")
            if resp.startswith("213 "):
                raw = resp[4:].strip()
                if len(raw) >= 14:
                    ts = int(datetime.strptime(raw[:14], "%Y%m%d%H%M%S").timestamp())
                    log.debug(f"MDTM {remote_path}: {datetime.fromtimestamp(ts)}")
                    return ts
        except Exception as e:
            log.debug(f"MDTM fallito {remote_path}: {e}")

        if entry.modified:
            ts = int(entry.modified.timestamp())
            log.debug(f"entry.modified {remote_path}: {entry.modified}")
            return ts

        return 0

    def _download(self, client: FTPClient, remote: str, local: Path):
        try:
            client.download(remote, str(local))
            log.debug(f"scaricato: {remote} -> {local}")
        except Exception as e:
            log.debug(f"download fallito {remote}: {e}")
            self._log(f"  ✗ errore download {local.name}: {e}", "err")

    def _upload(self, client: FTPClient, local: Path, remote: str):
        try:
            client.upload(str(local), remote)
            log.debug(f"caricato: {local} -> {remote}")
        except Exception as e:
            log.debug(f"upload fallito {remote}: {e}")
            self._log(f"  ✗ errore upload {local.name}: {e}", "err")
