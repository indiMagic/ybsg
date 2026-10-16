"""NFR-8: credentials live in the OS keychain only (Windows Credential Manager, macOS Keychain, Secret Service).
If no keychain is available YBSG refuses to store a secret; it never writes one to a project or a log."""
from __future__ import annotations

SERVICE = "ybsg"


class NoKeychain(RuntimeError):
    pass


def _kr():
    try:
        import keyring
        return keyring
    except Exception as e:
        raise NoKeychain("No OS keychain library found (pip install keyring). Credentials are not stored without one.") from e


def set_secret(name: str, value: str) -> None:
    _kr().set_password(SERVICE, name, value)


def get_secret(name: str) -> str | None:
    try:
        return _kr().get_password(SERVICE, name)
    except NoKeychain:
        return None


def delete_secret(name: str) -> None:
    _kr().delete_password(SERVICE, name)
