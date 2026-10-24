#!/usr/bin/env python3
"""fileSearch launcher.

Run from any directory:

    /path/to/tools/fileSearch/fileSearch.py

The execution directory gets a .fileSearch folder for config, index and cache.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Keep interpreter bytecode caches out of the source tree. fileSearch's own
# generated files live under ./.fileSearch as documented.
sys.dont_write_bytecode = True

sys.path.insert(0, str(Path(__file__).resolve().parent))

from file_search.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
