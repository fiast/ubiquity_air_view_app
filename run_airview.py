"""Запуск приложения (используется и PyInstaller-ом)."""

import sys

from airview.main import main

if __name__ == "__main__":
    sys.exit(main())
