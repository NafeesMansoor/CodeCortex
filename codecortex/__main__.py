"""Allow `python -m codecortex ...` for installations without the console script."""

import sys

from codecortex.cli import main

if __name__ == "__main__":
    sys.exit(main())
