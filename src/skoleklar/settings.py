"""The product's names for skolekit's state and secrets, and its model key.

skoleklar keeps the household's private files in `~/.config/skoleklar` (or
`AG_SCHOOL_HOME`): the school and profile settings, the database, the session,
artwork and, under `devices/`, the board's and local controller's files. A
folder from before the rename, `~/.config/ag-skoleintra`, is still used while
the new one doesn't exist. Nothing private lives in a checkout of the code.

Production and the household's machine still use the earlier names for the rest:
the school login in `AG_SCHOOL_USERNAME` and `AG_SCHOOL_PASSWORD`, and the
Keychain service `ag-skoleintra`, where the stored secrets are. Renaming those is
a coordinated change to the deploy scripts, the job's environment and the
Keychain. Importing skoleklar configures skolekit with these names, so the
toolkit itself names none of them.

The model key is the product's too: skolekit calls no model.
"""
from __future__ import annotations

import os
from pathlib import Path

from skolekit.state import Settings, configure, state_dir

SETTINGS = Settings(
    name="skoleklar",
    home_env="AG_SCHOOL_HOME",
    username_env="AG_SCHOOL_USERNAME",
    password_env="AG_SCHOOL_PASSWORD",
    keychain="ag-skoleintra",
    legacy_homes=("ag-skoleintra",),
)
KEYCHAIN_SERVICE = SETTINGS.keychain_service
configure(SETTINGS)

OPENROUTER_KEYCHAIN_ACCOUNT = "openrouter-api-key"
DEFAULT_MODEL = "openai/gpt-5.6-terra"


def devices_dir() -> Path:
    """The household's device files: the board's identity and the local controller's."""
    return state_dir() / "devices"


def openrouter_api_key() -> str | None:
    """The model provider's key: `OPENROUTER_API_KEY`, then the Keychain."""
    configured = os.environ.get("OPENROUTER_API_KEY")
    if configured:
        return configured
    import keyring

    return keyring.get_password(KEYCHAIN_SERVICE, OPENROUTER_KEYCHAIN_ACCOUNT)
