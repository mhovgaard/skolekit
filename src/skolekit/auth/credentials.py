"""Where the school login's username and password come from.

The environment comes first (`SKOLEKIT_USERNAME` and `SKOLEKIT_PASSWORD`, or a
product's own names; a cloud job gets them from its secret store), then the
macOS Keychain. The values never leave this module except to the browser login
that types them.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from skolekit.state import keychain_service, settings

ACCOUNT_ENTRY = "account"


class CredentialProvider(Protocol):
    def school_login(self) -> tuple[str, str] | None: ...


@dataclass(frozen=True)
class EnvironmentCredentials:
    def school_login(self) -> tuple[str, str] | None:
        username = os.environ.get(settings().username_env)
        password = os.environ.get(settings().password_env)
        return (username, password) if username and password else None


@dataclass(frozen=True)
class KeychainCredentials:
    # None: the configured product's service (skolekit.state.keychain_service).
    service: str | None = None

    @property
    def _service(self) -> str:
        return self.service or keychain_service()

    def school_login(self) -> tuple[str, str] | None:
        import keyring

        username = keyring.get_password(self._service, ACCOUNT_ENTRY)
        if not username:
            return None
        password = keyring.get_password(self._service, username)
        return (username, password) if password else None

    def save(self, username: str, password: str) -> None:
        import keyring

        previous = keyring.get_password(self._service, ACCOUNT_ENTRY)
        if previous and previous != username:
            try:
                keyring.delete_password(self._service, previous)
            except keyring.errors.PasswordDeleteError:
                pass
        keyring.set_password(self._service, ACCOUNT_ENTRY, username)
        keyring.set_password(self._service, username, password)

    def delete(self) -> None:
        import keyring

        username = keyring.get_password(self._service, ACCOUNT_ENTRY)
        for entry in (username, ACCOUNT_ENTRY):
            if not entry:
                continue
            try:
                keyring.delete_password(self._service, entry)
            except keyring.errors.PasswordDeleteError:
                pass


@dataclass(frozen=True)
class ChainedCredentials:
    providers: tuple[CredentialProvider, ...]

    def school_login(self) -> tuple[str, str] | None:
        for provider in self.providers:
            login = provider.school_login()
            if login:
                return login
        return None


def default_credentials() -> CredentialProvider:
    """The environment first, then the Keychain: today's order."""
    return ChainedCredentials((EnvironmentCredentials(), KeychainCredentials()))
