"""File storage for original uploads and rendered page images.

Local disk in development and on the demo host (a Render persistent disk). Files are never
served publicly: the API streams them to signed-in users after a role check and an audit row.
"""

from pathlib import Path

from intake.settings import get_settings


class LocalStorage:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def _path(self, key: str) -> Path:
        path = (self.base_dir / key).resolve()
        if not path.is_relative_to(self.base_dir.resolve()):
            raise ValueError("storage key escapes the storage directory")
        return path

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()


def get_storage() -> LocalStorage:
    return LocalStorage(get_settings().storage_dir)
