"""Smoke-тест GUI без экрана: создаёт окно, подключается, снимает скриншот.

Запуск: python tools/gui_smoke.py [--source mock|device] [--seconds N] [--out file.png]
Для device нужны --host/--user/--password (или JSON-настройки).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from airview.config import Settings  # noqa: E402
from airview.ui import MainWindow  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="mock")
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--out", default="gui_smoke.png")
    ap.add_argument("--host", default="192.168.1.20")
    ap.add_argument("--user", default="manager")
    ap.add_argument("--password", default="")
    args = ap.parse_args()

    settings = Settings(
        source=args.source,
        host=args.host,
        username=args.user,
        password=args.password,
        autosave=False,
        dump_enabled=False,
        attach_policy="restart",
        launch_timeout_s=60.0,
    )

    app = QApplication(sys.argv)
    window = MainWindow(settings)
    window.show()
    window.connect_btn.click()

    t0 = time.time()
    while time.time() - t0 < args.seconds:
        app.processEvents()
        time.sleep(0.01)

    out = Path(args.out)
    window.grab().save(str(out))
    frames = window.fps_label.text()
    device = window.device_label.text()
    title = window.windowTitle()

    window.close()
    app.processEvents()
    print(f"screenshot: {out.resolve()}")
    print("title:", title)
    print("device:", device)
    print("stats:", frames)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
