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
        if tls:
            self._ftp = ftplib.FTP_TLS()
        else:
            self._ftp = ftplib.FTP()
        self._ftp.connect(host, port, timeout=15)
        self._ftp.login(user, password)
        if tls:
            self._ftp.prot_p()  # encrypted data channel
        self._ftp.set_pasv(True)
        self.cwd = self._ftp.pwd()
        self.protocol = "FTPS" if tls else "FTP"

    def ls(self, path: str = ".") -> list[FileEntry]:
        entries: list[FileEntry] = []
        lines: list[str] = []
        self._ftp.dir(path, lines.append)
        for line in lines:
            entry = _parse_ftp_line(line)
            if entry:
                entries.append(entry)
        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        return entries

    def cd(self, path: str) -> str:
        self._ftp.cwd(path)
        self.cwd = self._ftp.pwd()
        return self.cwd

    def upload(self, local: str, remote: str, cb: ProgressCallback = None):
        size = os.path.getsize(local)
        transferred = 0

        def _cb(data: bytes):
            nonlocal transferred
            transferred += len(data)
            if cb:
                cb(transferred, size)

        with open(local, "rb") as f:
            self._ftp.storbinary(f"STOR {remote}", f, callback=_cb)

    def download(self, remote: str, local: str, cb: ProgressCallback = None):
        size = self._ftp.size(remote) or 0
        transferred = 0

        def _cb(data: bytes):
            nonlocal transferred
            transferred += len(data)
            f.write(data)
            if cb:
                cb(transferred, size)

        with open(local, "wb") as f:
            self._ftp.retrbinary(f"RETR {remote}", _cb)

    def mkdir(self, path: str):
        self._ftp.mkd(path)

    def delete(self, path: str, is_dir: bool = False):
        if is_dir:
            self._ftp.rmd(path)
        else:
            self._ftp.delete(path)

    def rename(self, old: str, new: str):
        self._ftp.rename(old, new)

    def disconnect(self):
        try:
            self._ftp.quit()
        except Exception:
            self._ftp.close()


def _parse_ftp_line(line: str) -> Optional[FileEntry]:
    """Parse Unix-style LIST output."""
    try:
        parts = line.split(None, 8)
        if len(parts) < 9:
            return None
        perms, _, _, _, size_str, month, day, year_or_time, name = parts
        is_dir = perms.startswith("d")
        size = int(size_str) if not is_dir else 0
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
