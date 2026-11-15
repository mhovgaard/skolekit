"""The household's profile: `school.toml` and `profile.toml`, both required.

The private school and profile files configure all identities. The cloud
checks them out from `state/` without writing them back. `profile write`
creates them explicitly; there are no bundled household defaults.
"""
from __future__ import annotations

from pathlib import Path

from skolekit.profile import SiteProfile, load_profile
from skolekit.state import state_dir


def state_home() -> Path:
    """The private state directory (skolekit.state.state_dir, with the product's names)."""
    return state_dir()


def snapshot_db_path() -> Path:
    """The household database; the removed legacy message database is never opened."""
    return state_dir() / "school-restricted.db"


def load_site_profile(home: Path | None = None) -> SiteProfile:
    """The household's profile from the state directory's two files; refused without them."""
    return load_profile(state_home() if home is None else home)
