#!/usr/bin/env python3
"""Render prepared state using the common dashboard contract."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from dashboard import main, render_html, render_png, validate

if __name__ == "__main__":
    main()
