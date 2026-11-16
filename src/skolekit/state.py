"""Where a run keeps its state, and the names it stores secrets under.

The state directory is read here and only here: the variable `SKOLEKIT_HOME`,
else ~/.config/skolekit. A product built on the toolkit gives its own names
with `configure`, so its state, environment and Keychain entries stay its own.
A run that works in a directory of its own, as a cloud job does, says so with
`using_state_dir`; the process environment is left alone, and the directory
reverts when the run ends.
"""
from __future__ import annotations

import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Settings:
    """The names the toolkit's state and secrets go under."""

    # Names the default state directory (~/.config/NAME) and, unless `keychain`
    # says otherwise, the Keychain service.
    name: str = "skolekit"
    # The variable that moves the state directory.
    home_env: str = "SKOLEKIT_HOME"
    # The variables a scheduled run passes the school login in.
    username_env: str = "SKOLEKIT_USERNAME"
    password_env: str = "SKOLEKIT_PASSWORD"
    # A Keychain service kept from an earlier name, so stored secrets are still found.
    keychain: str | None = None
    # Earlier state directory names (~/.config/OLD), used only while ~/.config/NAME
    # doesn't exist, so an installation keeps working until its folder is moved.
    legacy_homes: tuple[str, ...] = ()

    @property
    def default_home(self) -> Path:
        config = Path.home() / ".config"
        if not (config / self.name).exists():
            for old in self.legacy_homes:
                if (config / old).exists():
                    return config / old
        return config / self.name

    @property
    def keychain_service(self) -> str:
        return self.keychain or self.name


_settings: list[Settings] = [Settings()]
_overrides: list[Path] = []


def configure(settings: Settings) -> None:
    """Use a product's names from now on (a product calls this once, when it is imported)."""
    _settings[0] = settings


def settings() -> Settings:
    return _settings[0]


def keychain_service() -> str:
    """The macOS Keychain service the school login and the product's secrets are stored under."""
    return settings().keychain_service


def state_dir() -> Path:
    """The private state directory for this run."""
    if _overrides:
        return _overrides[-1]
    configured = os.environ.get(settings().home_env)
    if configured:
        return Path(configured).expanduser().resolve()
    return settings().default_home


@contextmanager
def using_state_dir(path: Path) -> Iterator[Path]:
    """Point this run at `path` until the block ends."""
    _overrides.append(path)
    try:
        yield path
    finally:
        _overrides.pop()


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(stat.S_IRWXU)
    return path


def write_private_json(path: Path, value: Any) -> None:
    ensure_private_dir(path.parent)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
    temporary.replace(path)
