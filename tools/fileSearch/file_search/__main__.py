"""Allow python -m file_search inside the tool directory."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
