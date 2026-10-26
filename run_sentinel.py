#!/usr/bin/env python3
"""Launcher for Claude Sentinel."""
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from claude_sentinel.app import main

if __name__ == "__main__":
    main()
