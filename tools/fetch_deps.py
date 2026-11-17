"""Скачивание и распаковка wheels из PyPI без pip (обход блокировки .whl.metadata).

Использование:  python tools/fetch_deps.py
Wheels кладутся в wheels/, содержимое разворачивается в libs/.
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
WHEELS = ROOT / "wheels"
LIBS = ROOT / "libs"

# пакет -> как выбирать файл из списка релиза
PACKAGES = {
    "numpy": lambda n: n.endswith("cp312-cp312-win_amd64.whl"),
    "pyqtgraph": lambda n: n.endswith("py3-none-any.whl"),
    "shiboken6": lambda n: n.endswith("win_amd64.whl") and "abi3" in n,
    "PySide6-Essentials": lambda n: n.endswith("win_amd64.whl") and "abi3" in n,
    "PySide6-Addons": lambda n: n.endswith("win_amd64.whl") and "abi3" in n,
    "colorama": lambda n: n.endswith("py3-none-any.whl"),
    # PyInstaller и его зависимости (для сборки exe)
    "pyinstaller": lambda n: n.endswith("-win_amd64.whl"),
    "altgraph": lambda n: n.endswith("py3-none-any.whl") or "py2.py3-none-any.whl" in n,
    "pefile": lambda n: n.endswith("py3-none-any.whl") or "py2.py3-none-any.whl" in n,
    "pyinstaller-hooks-contrib": lambda n: n.endswith("py3-none-any.whl"),
    "packaging": lambda n: n.endswith("py3-none-any.whl") or "py2.py3-none-any.whl" in n,
    "setuptools": lambda n: n.endswith("py3-none-any.whl"),
    "pywin32-ctypes": lambda n: n.endswith("py3-none-any.whl") or "py2.py3-none-any.whl" in n,
}


def pick_file(pkg: str, matcher) -> "tuple[str, str]":
    meta = requests.get(f"https://pypi.org/pypi/{pkg}/json", timeout=60).json()
    version = meta["info"]["version"]
    files = meta["releases"][version]
    for f in files:
        if matcher(f["filename"]):
            return f["filename"], f["url"]
    raise RuntimeError(f"no wheel for {pkg} {version}")


def download(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  cached {dest.name}")
        return
    print(f"  downloading {dest.name}")
    with requests.get(url, timeout=600, stream=True) as r:
        r.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    print(f"  saved {dest.stat().st_size // (1 << 20)} MB")


def extract(whl: Path, target: Path) -> None:
    with zipfile.ZipFile(whl) as zf:
        names = zf.namelist()
        # .data/ содержит скрипты и прочее, для Windows-пакетов не нужно
        names = [n for n in names if not n.endswith("/") and ".data/" not in n]
        zf.extractall(target, members=names)
    print(f"  extracted {whl.name} -> {target}")


def main() -> int:
    WHEELS.mkdir(exist_ok=True)
    LIBS.mkdir(exist_ok=True)
    for pkg, matcher in PACKAGES.items():
        print(pkg)
        name, url = pick_file(pkg, matcher)
        dest = WHEELS / name
        download(url, dest)
        extract(dest, LIBS)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
