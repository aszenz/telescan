#!/usr/bin/env python3
"""Run telescan without installing it:  ./telescan.py scan"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from telescan.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
