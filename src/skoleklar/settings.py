"""The product's names for skolekit's state and secrets, and its model key.

skoleklar keeps the names production and the household's machine already use:
the state directory `~/.config/ag-skoleintra` (or `AG_SCHOOL_HOME`), the school
login in `AG_SCHOOL_USERNAME` and `AG_SCHOOL_PASSWORD`, and the Keychain service
`ag-skoleintra`. Importing skoleklar configures skolekit with them, so the
toolkit itself names none of them. Renaming them is a coordinated change to
the deploy scripts and the job's environment (phase 10).

The model key is the product's too: skolekit calls no model.
"""
from __future__ import annotations

import os

from skolekit.state import Settings, configure

SETTINGS = Settings(
    name="ag-skoleintra",
    home_env="AG_SCHOOL_HOME",
    username_env="AG_SCHOOL_USERNAME",
    password_env="AG_SCHOOL_PASSWORD",
)
KEYCHAIN_SERVICE = SETTINGS.keychain_service
configure(SETTINGS)

OPENROUTER_KEYCHAIN_ACCOUNT = "openrouter-api-key"
DEFAULT_MODEL = "openai/gpt-5.6-terra"


def openrouter_api_key() -> str | None:
    """The model provider's key: `OPENROUTER_API_KEY`, then the Keychain."""
    configured = os.environ.get("OPENROUTER_API_KEY")
    if configured:
        return configured
    import keyring

    return keyring.get_password(KEYCHAIN_SERVICE, OPENROUTER_KEYCHAIN_ACCOUNT)
