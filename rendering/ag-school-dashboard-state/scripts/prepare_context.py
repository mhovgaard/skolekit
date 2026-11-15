#!/usr/bin/env python3
"""Compatibility entry point for the shared application implementation."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from skoleklar import dashboard_context as implementation
from skoleklar.dashboard_context import *
if __name__ == "__main__":
    implementation.main()
else:
    sys.modules[__name__] = implementation
