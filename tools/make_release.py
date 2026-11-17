"""Создание GitHub Release и загрузка zip-архива с exe.

Токен берётся из переменной окружения GH_TOKEN (fine-grained PAT:
Contents — Read and write на репозиторий fiast/ubiquity_air_view_app).

Запуск:
    $env:GH_TOKEN = "..."
    python tools/make_release.py
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import requests

REPO = "fiast/ubiquity_air_view_app"
TAG = "v1.0.0"
NAME = "airView Analyzer 1.0.0"
ASSET = Path("dist/AirViewAnalyzer-win64.zip")
API = "https://api.github.com"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


NOTES = """Первый публичный релиз.

**Что внутри**
- `AirViewAnalyzer.exe` — GUI-аналог airView для airOS 6.x (Windows x64, Python 3.12, PySide6 6.12).
- Живой спектр, Max Hold, Average и водопад; диапазоны строятся из `CONFIGURATION` устройства.
- Mock-режим без устройства и воспроизведение `.dat`-записей.

**Установка**
1. Скачать `AirViewAnalyzer-win64.zip`, распаковать (папку переносить целиком).
2. Запустить `AirViewAnalyzer.exe`.
3. SmartScreen может предупредить о неподписанном приложении:
   «Дополнительные сведения → Выполнить всё равно», либо заранее
   `Get-ChildItem .\\AirViewAnalyzer -Recurse | Unblock-File`.

**Проверено на**
PowerBeam M5 400, airOS XW.v6.3.11: 3776 бинов (4920–6100 МГц), ~7.5 fps,
смена полосы на лету, attach к запущенному AirView, headless-запись и replay.

**Исходники** — в ветке `main`, сборка: `build.bat`.
"""


def main() -> int:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("нет GH_TOKEN")
        return 2
    if not ASSET.is_file():
        print(f"нет файла {ASSET}")
        return 2

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # 1. релиз
    payload = {
        "tag_name": TAG,
        "target_commitish": "main",
        "name": NAME,
        "body": NOTES,
        "draft": False,
        "prerelease": False,
    }
    resp = requests.post(f"{API}/repos/{REPO}/releases", headers=headers, json=payload, timeout=60)
    if resp.status_code == 422:
        print("релиз уже существует, обновляю описание")
        rel = requests.get(f"{API}/repos/{REPO}/releases/tags/{TAG}", headers=headers, timeout=60).json()
        resp = requests.patch(f"{API}/repos/{REPO}/releases/{rel['id']}", headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    release = resp.json()
    upload_url = release["upload_url"].split("{")[0]
    print("release:", release["html_url"])

    # 2. ассет
    data = ASSET.read_bytes()
    up_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/zip",
    }
    up = requests.post(
        f"{upload_url}?name={ASSET.name}",
        headers=up_headers,
        data=data,
        timeout=600,
    )
    up.raise_for_status()
    asset = up.json()
    print("asset:", asset["browser_download_url"])
    print("sha256:", sha256(ASSET))
    return 0


if __name__ == "__main__":
    sys.exit(main())
