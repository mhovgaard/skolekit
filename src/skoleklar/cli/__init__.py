"""The product CLI: parse flags, load the selected command, and format errors."""
from __future__ import annotations

import asyncio
import inspect

from skoleklar.cli.parser import build_parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = args.handler(args)
        raise SystemExit(asyncio.run(result) if inspect.isawaitable(result) else result)
    except ValueError as error:
        parser.error(str(error))
    except ModuleNotFoundError as error:
        # Only optional libraries get an installation hint; internal import bugs still surface.
        extras = {"playwright": "browser", "PIL": "board", "fastapi": "server",
                  "uvicorn": "server", "google": "gcp", "mcp": "mcp"}
        extra = extras.get((error.name or "").split(".")[0])
        if extra is None:
            raise
        parser.error(f"This command needs the {extra} extra: uv sync --extra {extra}")
