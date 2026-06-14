# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (editable, system-wide)
pip install -e . --break-system-packages

# Run
python3 -m ftui.app
# or after install:
ftui
```

There are no tests and no linter configured in this repo.

## Commit messages

Non inserire firme, co-autore o credenziali IA nei messaggi di commit. Usa solo messaggi standard in formato Conventional Commits.

## Project structure

```
ftui/
├── app.py        — TUI principale (prompt_toolkit): PaneState, XferState, FtuiApp, render_*
├── protocols.py  — FTP/FTPS/SFTP/SCP clients + FileEntry + connect() factory
├── bookmarks.py  — salvataggio/lettura ~/.config/ftui/bookmarks.json
└── __init__.py
pyproject.toml
```

## Architecture

`ftui` is a dual-pane TUI file transfer client (FTP/FTPS/SFTP/SCP). The active implementation lives entirely in `ftui/app.py` using **prompt_toolkit** (`FormattedText` tuple API). This was deliberately chosen over Textual for low overhead on Raspberry Pi / slow SSH sessions.

### Active app (`app.py`)

Three core classes:

- **`PaneState`** — mutable state for one pane: `path`, `entries` (`list[FileEntry]`), `cursor`, `offset`, `client`, `connected`. `_all()` prepends a `..` entry when not at root. `move()` keeps `offset` in sync for scrolling.
- **`XferState`** — progress state for the active transfer: `transferred`, `total`, `done_files`, `total_files`. `bar_ft()` returns `FormattedText` fragments for the progress bar.
- **`FtuiApp`** — owns two `PaneState` instances (`left`/`right`), one `XferState`, a `ThreadPoolExecutor(max_workers=2)`, and the prompt_toolkit `Application`. All file I/O is submitted to the thread pool; UI redraws are triggered via `self._app.invalidate()`.

Rendering is done by pure `render_*()` functions that return `FormattedText`. There are no reactive/observer hooks — the app redraws only when explicitly invalidated (`_redraw()`).

Modal state is tracked via `self.modal` (string: `"connect"` | `"bookmarks"` | `"mkdir"` | `"rename"` | `"confirm"` | `None`). When a modal is active, `left_ft()` renders the modal instead of the left pane. Key bindings use `prompt_toolkit.filters.Condition` to scope handlers to the correct modal.

### Protocol layer (`protocols.py`)

All four protocol clients (`FTPClient`, `SFTPClient`, `SCPClient`) expose an identical interface:

```python
ls(path) -> list[FileEntry]
cd(path) -> str          # returns resolved cwd
upload(local, remote, cb)
download(remote, local, cb)
upload_dir(local_dir, remote_dir, cb)
download_dir(remote_dir, local_dir, cb)
mkdir(path)
delete(path, is_dir=False)
rename(old, new)
disconnect()
```

`SCPClient` wraps `SFTPClient` internally and falls back to SFTP transfers if the `scp` package is not installed. `FTPClient` holds an `RLock` and auto-reconnects on broken pipe errors. The factory function `connect(protocol, host, port, user, password, key_path)` dispatches to the right class.

### Bookmarks (`bookmarks.py`)

Saved to `~/.config/ftui/bookmarks.json` as a dict keyed by bookmark name. Fields: `name`, `protocol`, `host`, `port`, `user`, `password`, `key_path`, `remote_path`.

### Key bindings

Function keys (F2–F9) all have single-letter aliases: `c` (connect), `b` (bookmarks), `t` (transfer), `m` (mkdir), `d` (delete), `r` (rename).
