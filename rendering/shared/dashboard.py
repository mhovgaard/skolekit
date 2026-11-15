#!/usr/bin/env python3
"""Compatibility entry point for the shared application implementation."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from skoleklar import dashboard_renderer as implementation
from skoleklar.dashboard_renderer import *
if __name__ == "__main__":
    implementation.main()
else:
    sys.modules[__name__] = implementation
