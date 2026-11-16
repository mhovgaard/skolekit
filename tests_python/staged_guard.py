"""The pre-commit check: the household guard run over what is about to be committed.

It reads each added or changed file as staged, not as it is on disk, so a name
removed from the working copy but still staged is caught. Like the tracked-file
test it reports a file and the kind of identifier, never the identifier itself.
Without the owner's files it still refuses real-looking addresses, home paths and
deployed gateway hostnames. Enable it once per clone:

    git config core.hooksPath .githooks
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests_python.household_guard import BINARY, OWNER_HOME, household_patterns, kinds_in

# Its list of month names is not about anyone; the tracked-file test skips it too.
GUARD = "tests_python/household_guard.py"


def staged_offenders(repo: Path) -> list[str]:
    """'path: kind' for every added or changed staged file that holds an identifier or trace."""
    names = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
                           cwd=repo, capture_output=True, check=True).stdout.decode("utf-8").split("\0")
    patterns = household_patterns() or []
    found = []
    for name in filter(None, names):
        if Path(name).suffix in BINARY or name == GUARD:
            continue
        staged = subprocess.run(["git", "cat-file", "blob", f":{name}"],
                                cwd=repo, capture_output=True, check=True).stdout
        found += [f"{name}: {kind}" for kind in kinds_in(staged.decode("utf-8", errors="ignore"), patterns)]
    return sorted(set(found))


def main() -> int:
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True)
    found = staged_offenders(Path(top.stdout.strip()))
    if household_patterns() is None:
        # Say so: on the owner's machine this means the names aren't being checked.
        where = str(OWNER_HOME).replace(str(Path.home()), "~", 1)
        print(f"privacy check: no household files in {where}; checking only for addresses, "
              "home paths and gateway hosts", file=sys.stderr)
    for line in found:
        print(f"privacy check: {line}", file=sys.stderr)
    if found:
        print("privacy check: commit refused. Replace each value with an invented one "
              "(Alfa, Beta, an example.* address), stage it again and commit.", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
