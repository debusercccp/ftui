"""
Protocol abstraction: FTP, FTPS, SFTP, SCP
All clients expose the same interface:
  ls(path) -> list[FileEntry]
  cd(path) -> str  (new cwd)
  upload(local, remote, progress_cb)
  download(remote, local, progress_cb)
  mkdir(path)
  delete(path)
  rename(old, new)
  disconnect()
"""

from __future__ import annotations
import ftplib
import os
import stat
import threading
import contextlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional


@dataclass
class FileEntry:
    name: str
    size: int
    is_dir: bool
    modified: Optional[datetime] = None
    permissions: str = ""

    @property
    def size_human(self) -> str:
        if self.is_dir:
            return "<DIR>"
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if self.size < 1024:
                return f"{self.size:.1f} {unit}"
            self.size /= 1024
        return f"{self.size:.1f} PB"


ProgressCallback = Callable[[int, int], None]  # (transferred, total)


# ─────────────────────────────────────────────
# FTP / FTPS
# ─────────────────────────────────────────────
class FTPClient:
    def __init__(self, host: str, port: int, user: str, password: str, tls: bool = False):
        self._host     = host
        self._port     = port
        self._user     = user
        self._password = password
        self._tls      = tls
        self._ftp      = None
        self._lock     = threading.RLock()  # Usiamo RLock per i trasferimenti di intere cartelle
        self._connect()
        self.protocol = "FTPS" if tls else "FTP"

    @contextlib.contextmanager
    def _busy_lock(self):
        """Se c'è un trasferimento in corso, blocca il nuovo comando istantaneamente senza far freezare la UI."""
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("Operazione rifiutata: trasferimento in corso. Attendi la fine.")
        try:
            yield
        finally:
            self._lock.release()

    def _connect(self):
        if self._tls:
            self._ftp = ftplib.FTP_TLS()
        else:
            self._ftp = ftplib.FTP()
        self._ftp.connect(self._host, self._port, timeout=30)
        self._ftp.encoding = "latin-1"
        self._ftp.login(self._user, self._password)
        if self._tls:
            self._ftp.prot_p()
        self._ftp.set_pasv(True)
        self.cwd = self._ftp.pwd()

    def _reconnect(self):
        try:
            self._ftp.close()
        except Exception:
            pass
        self._connect()
        try:
            self._ftp.cwd(self.cwd)
        except Exception:
            self.cwd = self._ftp.pwd()

    def _run(self, fn):
        try:
            return fn()
        except ftplib.error_reply as e:
            raise
        except (BrokenPipeError, EOFError, ConnectionResetError, OSError):
            self._reconnect()
            return fn()

    def ls(self, path: str = ".") -> list[FileEntry]:
        with self._busy_lock():
            def _do():
                lines: list[str] = []
                try:
                    self._ftp.dir(f"-a {path}", lines.append)
                except Exception:
                    lines = []
                    self._ftp.dir(path, lines.append)
                entries = []
                for line in lines:
                    entry = _parse_ftp_line(line)
                    if entry and entry.name not in (".", ".."):
                        entries.append(entry)
                entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
                return entries
            return self._run(_do)

    def cd(self, path: str) -> str:
        with self._busy_lock():
            def _do():
                self._ftp.cwd(path)
                self.cwd = self._ftp.pwd()
                return self.cwd
            return self._run(_do)

    def upload(self, local: str, remote: str, cb: ProgressCallback = None):
        with self._busy_lock():
            try:
                size = os.path.getsize(local)
            except Exception:
                size = 0

            def _do():
                transferred = 0
                with open(local, "rb") as f:
                    def _cb(data: bytes):
                        nonlocal transferred
                        transferred += len(data)
                        if cb:
                            cb(transferred, size)
                    self._ftp.storbinary(f"STOR {remote}", f, callback=_cb)

            self._run(_do)

    def download(self, remote: str, local: str, cb: ProgressCallback = None):
        with self._busy_lock():
            try:
                size = self._ftp.size(remote) or 0
            except Exception:
                size = 0

            def _do_transfer():
                transferred = 0
                with open(local, "wb") as out:
                    def _chunk(data: bytes):
                        nonlocal transferred
                        out.write(data)
                        transferred += len(data)
                        if cb:
                            cb(transferred, size)
                    
                    self._ftp.retrbinary(f"RETR {remote}", _chunk)

            self._run(_do_transfer)

    def mkdir(self, path: str):
        with self._busy_lock():
            self._run(lambda: self._ftp.mkd(path))

    def delete(self, path: str, is_dir: bool = False):
        with self._busy_lock():
            def _do():
                if is_dir:
                    for cmd in (
                        lambda: self._ftp.rmd(path),
                        lambda: self._ftp.voidcmd(f"XRMD {path}"),
                        lambda: self._ftp.voidcmd(f"SITE RMDIR {path}"),
                    ):
                        try:
                            cmd()
                            return
                        except ftplib.error_perm as e:
                            last = e
                    raise last
                else:
                    self._ftp.delete(path)
            self._run(_do)

    def rename(self, old: str, new: str):
        with self._busy_lock():
            self._run(lambda: self._ftp.rename(old, new))

    def upload_dir(self, local_dir: str, remote_dir: str, cb: ProgressCallback = None):
        with self._busy_lock():
            import posixpath
            try:
                self.mkdir(remote_dir)
            except Exception:
                pass
            for item in sorted(Path(local_dir).iterdir()):
                remote_path = posixpath.join(remote_dir, item.name)
                if item.is_dir():
                    self.upload_dir(str(item), remote_path, cb)
                else:
                    self.upload(str(item), remote_path, cb)

    def download_dir(self, remote_dir: str, local_dir: str, cb: ProgressCallback = None):
        with self._busy_lock():
            import posixpath
            Path(local_dir).mkdir(parents=True, exist_ok=True)
            entries = self.ls(remote_dir)
            for entry in entries:
                remote_path = posixpath.join(remote_dir, entry.name)
                local_path = os.path.join(local_dir, entry.name)
                if entry.is_dir:
                    self.download_dir(remote_path, local_path, cb)
                else:
                    self.download(remote_path, local_path, cb)

    def disconnect(self):
        with self._busy_lock():
            try:
                self._ftp.quit()
            except Exception:
                self._ftp.close()

def _parse_ftp_line(line: str) -> Optional[FileEntry]:
    """Parse Unix-style LIST output."""
    try:
        line = line.encode("latin-1", errors="replace").decode("latin-1")
        parts = line.split(None, 8)
        if len(parts) < 9:
            return None
        perms, _, _, _, size_str, month, day, year_or_time, name = parts
        # alcuni server restituiscono il path completo nel nome — teniamo solo il basename
        name = name.strip().split("/")[-1]
        if not name:
            return None
        is_dir = perms.startswith("d")
        size   = int(size_str) if not is_dir else 0
        return FileEntry(name=name, size=size, is_dir=is_dir, permissions=perms)
    except Exception:
        return None


# ─────────────────────────────────────────────
# SFTP
# ─────────────────────────────────────────────
class SFTPClient:
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: Optional[str] = None,
        key_path: Optional[str] = None,
    ):
        import paramiko

        self._ssh = paramiko.SSHClient()
        self._ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs: dict = dict(hostname=host, port=port, username=user, timeout=15)
        if key_path:
            kwargs["key_filename"] = key_path
        if password:
            kwargs["password"] = password
        self._ssh.connect(**kwargs)
        self._sftp = self._ssh.open_sftp()
        self.cwd = self._sftp.getcwd() or "/"
        self.protocol = "SFTP"

    def ls(self, path: str = ".") -> list[FileEntry]:
        entries = []
        for attr in self._sftp.listdir_attr(path):
            is_dir = stat.S_ISDIR(attr.st_mode or 0)
            entries.append(
                FileEntry(
                    name=attr.filename,
                    size=attr.st_size or 0,
                    is_dir=is_dir,
                    modified=datetime.fromtimestamp(attr.st_mtime or 0),
                    permissions=_mode_to_str(attr.st_mode or 0),
                )
            )
        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        return entries

    def cd(self, path: str) -> str:
        self._sftp.chdir(path)
        self.cwd = self._sftp.getcwd() or path
        return self.cwd

    def upload(self, local: str, remote: str, cb: ProgressCallback = None):
        size = os.path.getsize(local)

        def _cb(transferred, total):
            if cb:
                cb(transferred, size)

        self._sftp.put(local, remote, callback=_cb)

    def download(self, remote: str, local: str, cb: ProgressCallback = None):
        attrs = self._sftp.stat(remote)
        size = attrs.st_size or 0

        def _cb(transferred, total):
            if cb:
                cb(transferred, size)

        self._sftp.get(remote, local, callback=_cb)

    def mkdir(self, path: str):
        self._sftp.mkdir(path)

    def delete(self, path: str, is_dir: bool = False):
        if is_dir:
            self._sftp.rmdir(path)
        else:
            self._sftp.remove(path)

    def rename(self, old: str, new: str):
        self._sftp.rename(old, new)

    def upload_dir(self, local_dir: str, remote_dir: str, cb: ProgressCallback = None):
        import posixpath
        try:
            self._sftp.mkdir(remote_dir)
        except Exception:
            pass
        for item in sorted(Path(local_dir).iterdir()):
            remote_path = posixpath.join(remote_dir, item.name)
            if item.is_dir():
                self.upload_dir(str(item), remote_path, cb)
            else:
                self.upload(str(item), remote_path, cb)

    def download_dir(self, remote_dir: str, local_dir: str, cb: ProgressCallback = None):
        import posixpath
        Path(local_dir).mkdir(parents=True, exist_ok=True)
        entries = self.ls(remote_dir)
        for entry in entries:
            remote_path = posixpath.join(remote_dir, entry.name)
            local_path = os.path.join(local_dir, entry.name)
            if entry.is_dir:
                self.download_dir(remote_path, local_path, cb)
            else:
                self.download(remote_path, local_path, cb)

    def disconnect(self):
        self._sftp.close()
        self._ssh.close()


def _mode_to_str(mode: int) -> str:
    flags = ["d", "r", "w", "x", "r", "w", "x", "r", "w", "x"]
    bits = [
        stat.S_ISDIR(mode),
        bool(mode & stat.S_IRUSR),
        bool(mode & stat.S_IWUSR),
        bool(mode & stat.S_IXUSR),
        bool(mode & stat.S_IRGRP),
        bool(mode & stat.S_IWGRP),
        bool(mode & stat.S_IXGRP),
        bool(mode & stat.S_IROTH),
        bool(mode & stat.S_IWOTH),
        bool(mode & stat.S_IXOTH),
    ]
    return "".join(c if b else "-" for c, b in zip(flags, bits))


# ─────────────────────────────────────────────
# SCP  (uses paramiko SSH underneath)
# ─────────────────────────────────────────────
class SCPClient:
    """SCP wraps SFTP for file transfer but uses SCP semantics for the UI."""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: Optional[str] = None,
        key_path: Optional[str] = None,
    ):
        # SCP piggybacks on SFTP for listing/navigation
        self._inner = SFTPClient(host, port, user, password, key_path)
        self.cwd = self._inner.cwd
        self.protocol = "SCP"

    def ls(self, path: str = ".") -> list[FileEntry]:
        return self._inner.ls(path)

    def cd(self, path: str) -> str:
        result = self._inner.cd(path)
        self.cwd = result
        return result

    def upload(self, local: str, remote: str, cb: ProgressCallback = None):
        import paramiko
        from scp import SCPClient as _SCP  # type: ignore

        transport = self._inner._ssh.get_transport()
        with _SCP(transport, progress=lambda f, s, t: cb(s, t) if cb else None) as scp:
            scp.put(local, remote)

    def download(self, remote: str, local: str, cb: ProgressCallback = None):
        import paramiko
        try:
            from scp import SCPClient as _SCP  # type: ignore
            transport = self._inner._ssh.get_transport()
            with _SCP(transport, progress=lambda f, s, t: cb(s, t) if cb else None) as scp:
                scp.get(remote, local)
        except ImportError:
            # fallback to SFTP if scp package not installed
            self._inner.download(remote, local, cb)

    def mkdir(self, path: str):
        self._inner.mkdir(path)

    def delete(self, path: str, is_dir: bool = False):
        self._inner.delete(path, is_dir)

    def rename(self, old: str, new: str):
        self._inner.rename(old, new)

    def upload_dir(self, local_dir: str, remote_dir: str, cb: ProgressCallback = None):
        self._inner.upload_dir(local_dir, remote_dir, cb)

    def download_dir(self, remote_dir: str, local_dir: str, cb: ProgressCallback = None):
        self._inner.download_dir(remote_dir, local_dir, cb)

    def disconnect(self):
        self._inner.disconnect()


# ─────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────
def connect(
    protocol: str,
    host: str,
    port: int,
    user: str,
    password: Optional[str] = None,
    key_path: Optional[str] = None,
) -> "FTPClient | SFTPClient | SCPClient":
    p = protocol.upper()
    if p == "FTP":
        return FTPClient(host, port, user, password or "", tls=False)
    elif p == "FTPS":
        return FTPClient(host, port, user, password or "", tls=True)
    elif p == "SFTP":
        return SFTPClient(host, port, user, password, key_path)
    elif p == "SCP":
        return SCPClient(host, port, user, password, key_path)
    else:
        raise ValueError(f"Unknown protocol: {protocol}")
