"""Проверка смены диапазона на лету: Full range → пресет 5150–5250 МГц."""

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
    ap.add_argument("--host", default="192.168.1.20")
    ap.add_argument("--user", default="manager")
    ap.add_argument("--password", default="")
    ap.add_argument("--band", type=int, default=1, help="индекс пресета в combo (0 = full range)")
    ap.add_argument("--seconds", type=float, default=45.0)
    ap.add_argument("--out", default="gui_band.png")
    ap.add_argument("--band-list", action="store_true")
    args = ap.parse_args()

    settings = Settings(
        source="device",
        host=args.host,
        username=args.user,
        password=args.password,
        autosave=False,
        dump_enabled=False,
        attach_policy="restart",
    )
    app = QApplication(sys.argv)
    window = MainWindow(settings)
    window.show()
    window.connect_btn.click()

    t0 = time.time()
    switched = False
    while time.time() - t0 < args.seconds:
        app.processEvents()
        time.sleep(0.01)
        if args.band_list:
            print("bands:", [window.band_combo.itemText(i) for i in range(window.band_combo.count())])
            break
        if not switched and "device:" in window.device_label.text() and "—" not in window.device_label.text():
            time.sleep(2.0)
            app.processEvents()
            window.band_combo.setCurrentIndex(args.band)
            time.sleep(4.0)
            app.processEvents()
            worker = window._worker
            src = getattr(worker, "_source", None) if worker is not None else None
            if src is not None:
                print(f"range after switch: {src.start_hz / 1e6:.1f}-{src.end_hz / 1e6:.1f} MHz, bins={src.bins}")
            switched = True

    window.grab().save(args.out)
    print("title:", window.windowTitle())
    print("device:", window.device_label.text())
    print("range:", window.freq_label.text())
    print("stats:", window.fps_label.text())
    window.close()
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
