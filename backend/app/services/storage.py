"""File storage behind an interface: local disk now, S3-compatible later.

Keys are relative paths like ``org/<organization_id>/messages/<message_id>/<uuid>.jpg``; every
file of an organization lives under ``org/<organization_id>/`` so account deletion can remove
them all at once.
"""

import shutil
from pathlib import Path
from typing import Protocol

from app.core.config import BACKEND_DIR, get_settings


class Storage(Protocol):
    def save(self, key: str, content: bytes, content_type: str) -> None: ...

    def read(self, key: str) -> bytes: ...

    def delete_prefix(self, prefix: str) -> None: ...


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError(f"Invalid storage key {key!r}")
        return path

    def save(self, key: str, content: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete_prefix(self, prefix: str) -> None:
        path = self._path(prefix)
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()


_override: Storage | None = None


def set_storage(storage: Storage | None) -> None:
    """Tests use a temporary folder; None restores the configured storage."""
    global _override
    _override = storage


def get_storage() -> Storage:
    if _override is not None:
        return _override
    settings = get_settings()
    if settings.STORAGE_BACKEND == "local":
        root = Path(settings.STORAGE_LOCAL_PATH)
        return LocalStorage(root if root.is_absolute() else BACKEND_DIR / root)
    raise NotImplementedError(f"Storage backend {settings.STORAGE_BACKEND!r} is not implemented")


def organization_prefix(organization_id: object) -> str:
    return f"org/{organization_id}"
