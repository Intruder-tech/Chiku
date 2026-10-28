"""PyInstaller entry point for both Chiku.exe (windowed) and chiku-cli.exe (console)."""

import multiprocessing
import sys

from chiku.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
