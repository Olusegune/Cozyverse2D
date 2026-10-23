"""Provider API keys live in the OS credential vault (Windows Credential Manager). Never in the DB, files or logs."""
from __future__ import annotations

from typing import Optional, Protocol

SERVICE = "CozyVerse2D"


class SecretError(RuntimeError):
    pass


class SecretStore(Protocol):
    def get(self, name: str) -> Optional[str]: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class KeyringStore:
    def __init__(self) -> None:
        import keyring
        self._kr = keyring

    def get(self, name: str) -> Optional[str]:
        try:
            return self._kr.get_password(SERVICE, name)
        except Exception as e:  # noqa: BLE001
            raise SecretError("could not read the credential store") from e

    def set(self, name: str, value: str) -> None:
        try:
            self._kr.set_password(SERVICE, name, value)
        except Exception as e:  # noqa: BLE001
            raise SecretError("could not write the credential store") from e

    def delete(self, name: str) -> None:
        try:
            self._kr.delete_password(SERVICE, name)
        except self._kr.errors.PasswordDeleteError:
            pass
        except Exception as e:  # noqa: BLE001
            raise SecretError("could not delete from the credential store") from e


class MemoryStore:
    """Tests only."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, name: str) -> Optional[str]:
        return self.data.get(name)

    def set(self, name: str, value: str) -> None:
        self.data[name] = value

    def delete(self, name: str) -> None:
        self.data.pop(name, None)


def default_store() -> SecretStore:
    return KeyringStore()
