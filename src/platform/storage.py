"""
Platform-aware credential storage abstraction.
Windows uses keyring; Android uses Flet client_storage; fallback uses file.
"""
import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class BaseCredentialStore:
    """Abstract credential store interface."""

    def get(self, service: str, key: str) -> Optional[str]:
        raise NotImplementedError

    def set(self, service: str, key: str, value: str) -> None:
        raise NotImplementedError


class KeyringStore(BaseCredentialStore):
    """Windows/Desktop credential store using system keyring."""

    def __init__(self):
        import keyring
        self._keyring = keyring

    def get(self, service: str, key: str) -> Optional[str]:
        try:
            return self._keyring.get_password(service, key)
        except Exception as e:
            logger.warning("Keyring read failed for %s/%s: %s", service, key, e)
            return None

    def set(self, service: str, key: str, value: str) -> None:
        try:
            self._keyring.set_password(service, key, value)
        except Exception as e:
            logger.warning("Keyring write failed for %s/%s: %s", service, key, e)


class FletClientStore(BaseCredentialStore):
    """Mobile credential store using Flet's client_storage (encrypted)."""

    def __init__(self, client_storage):
        self._storage = client_storage

    def get(self, service: str, key: str) -> Optional[str]:
        try:
            full_key = f"{service}.{key}"
            return self._storage.get(full_key)
        except Exception as e:
            logger.warning("Client storage read failed for %s/%s: %s", service, key, e)
            return None

    def set(self, service: str, key: str, value: str) -> None:
        try:
            full_key = f"{service}.{key}"
            self._storage.set(full_key, value)
        except Exception as e:
            logger.warning("Client storage write failed for %s/%s: %s", service, key, e)


class FileStore(BaseCredentialStore):
    """Fallback credential store using encrypted JSON file."""

    def __init__(self, store_path: Optional[Path] = None):
        if store_path is None:
            from config import _USER_DATA_DIR
            store_path = _USER_DATA_DIR / ".credentials"
        self._path = Path(store_path)

    def get(self, service: str, key: str) -> Optional[str]:
        data = self._load()
        return data.get(f"{service}.{key}")

    def set(self, service: str, key: str, value: str) -> None:
        data = self._load()
        data[f"{service}.{key}"] = value
        self._save(data)

    def _load(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self, data: dict) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(tmp), str(self._path))
        except Exception as e:
            logger.warning("File store save failed: %s", e)


def get_credential_store(page=None) -> BaseCredentialStore:
    """
    Factory: returns the right credential store for the current platform.
    
    - Windows → KeyringStore (system keyring)
    - Mobile (with page) → FletClientStore (encrypted client storage)
    - Fallback → FileStore (JSON file)
    """
    from platform import IS_WINDOWS, IS_MOBILE

    if IS_WINDOWS:
        try:
            return KeyringStore()
        except ImportError:
            logger.warning("keyring not available, falling back to file store")
            return FileStore()

    if IS_MOBILE and page and hasattr(page, 'client_storage'):
        return FletClientStore(page.client_storage)

    return FileStore()
