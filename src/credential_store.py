"""Only use the operating system's native credential vault; never a text fallback."""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path


class CredentialStore:
    def __init__(self, directory: Path, disabled: bool = False):
        digest = hashlib.sha256(os.path.normcase(str(directory.resolve())).encode()).hexdigest()[:16]
        # Stable vault identifier: changing the display name must not lose keys.
        self.service = "AWEAR Personal Lab " + digest
        self.backend = None
        if disabled:
            return
        try:
            if sys.platform == "darwin":
                from keyring.backends.macOS import Keyring
                self.backend = Keyring()
            elif sys.platform == "win32":
                from keyring.backends.Windows import WinVaultKeyring
                self.backend = WinVaultKeyring()
        except (ImportError, RuntimeError):
            pass

    @property
    def available(self) -> bool:
        return self.backend is not None

    def get(self) -> str:
        try:
            return self.backend.get_password(self.service, "api-key") or "" if self.backend else ""
        except Exception as error:
            raise ValueError("Could not read the system credential store. Enter your key again or use it for this session.") from error

    def save(self, key: str) -> None:
        if not self.backend:
            raise ValueError("Secure storage is unavailable. Uncheck Remember to use your key for this session.")
        try:
            self.backend.set_password(self.service, "api-key", key)
        except Exception as error:
            raise ValueError("The system could not save your key. Uncheck Remember to use it for this session.") from error

    def delete(self) -> None:
        if not self.backend:
            raise ValueError("The system credential store is unavailable.")
        try:
            if self.get():
                self.backend.delete_password(self.service, "api-key")
        except Exception as error:
            raise ValueError("The system could not remove the saved key.") from error
