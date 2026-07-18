"""Allow ``python3 -m trading.cli`` to invoke the CLI."""
from .main import main

if __name__ == "__main__":
    raise SystemExit(main())
