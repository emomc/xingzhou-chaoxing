"""Private state files, authenticated encryption, and a single-instance lock."""

import hashlib
import hmac
import os
from pathlib import Path
import secrets

from cryptography.fernet import Fernet


class StateInUse(RuntimeError):
    pass


def private_file(path, create):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Secret files cannot be symbolic links")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_bytes().strip()
    value = create()
    with os.fdopen(fd, "wb") as stream:
        stream.write(value + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    return value


class StateLock:
    def __init__(self, directory):
        self.path = Path(directory) / "service.lock"
        if self.path.is_symlink():
            raise ValueError("Lock files cannot be symbolic links")
        self.stream = self.path.open("a+b")
        self.stream.seek(0, os.SEEK_END)
        if self.stream.tell() == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise StateInUse("This state directory is already in use") from None

    def close(self):
        if self.stream.closed:
            return
        self.stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.stream, fcntl.LOCK_UN)
        self.stream.close()


class Secrets:
    def __init__(self, directory, token=None):
        directory = Path(directory)
        if directory.is_symlink():
            raise ValueError("The state directory cannot be a symbolic link")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        key_path = directory / "credentials.key"
        if (directory / "service.sqlite3").exists() and not key_path.exists():
            raise ValueError("credentials.key is missing; restore it with the database")
        self.key = private_file(key_path, Fernet.generate_key)
        self.cipher = Fernet(self.key)
        self.token = token or private_file(
            directory / "api-token", lambda: secrets.token_urlsafe(36).encode("ascii")
        ).decode("ascii")
        if not 32 <= len(self.token) <= 512 or any(
            not 33 <= ord(char) <= 126 for char in self.token
        ):
            raise ValueError("The service token must contain 32..512 visible ASCII characters")

    def fingerprint(self, username):
        return hmac.new(self.key, username.encode("utf-8"), hashlib.sha256).hexdigest()
