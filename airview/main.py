"""Точка входа airView Analyzer.

Режимы:
    airview                  — графический интерфейс
    airview --check          — подключиться, показать CONFIGURATION и выйти
    airview --headless       — без GUI: читать кадры (для отладки/записи)
    airview --mock           — GUI с синтетическим спектром
    airview --replay FILE    — GUI с воспроизведением записанного .dat
"""

from __future__ import annotations

import sys
import time
from typing import Optional

from . import __version__
from .airview_client import AirViewClient, AirViewError
from .config import Settings, load_from_cli
from .datasource import DataSource, DataSourceError, MockDataSource, ReplayDataSource


def make_source(settings: Settings, verbose: bool = True) -> DataSource:
    """Создаёт источник данных по настройкам (без Qt)."""
    if settings.source == "mock":
        return MockDataSource(
            start_mhz=settings.mock_start_mhz,
            end_mhz=settings.mock_end_mhz,
            bins=settings.mock_bins,
        )
    if settings.source == "replay":
        return ReplayDataSource(settings.replay_file)

    def status(text: str, level: str = "info") -> None:
        if verbose:
            print(f"[{level}] {text}")

    return AirViewClient(settings, status_cb=status)


def cmd_check(settings: Settings) -> int:
    """Быстрая проверка связи: CONFIGURATION, диапазон, пара кадров."""
    client = AirViewClient(settings, status_cb=lambda t, l="info": print(f"[{l}] {t}"))
    try:
        attach = client.probe_running() and settings.attach_policy != "restart"
        client.start(attach=attach)
        cfg = client.device
        if cfg is None:
            print("CONFIGURATION не получена")
            return 2
        print()
        print("Устройство :", cfg.model_full)
        print("Прошивка   :", cfg.version, "|", cfg.build_date)
        print("MAC        :", cfg.mac)
        print("Диапазон   :", f"{cfg.min_freq_mhz}-{cfg.max_freq_mhz} MHz")
        print("Бин        :", cfg.bin_hz / 1e3, "kHz")
        print("Пресеты:")
        for i, band in enumerate(cfg.bands):
            print(f"  [{i}] {band.label} ({band.start_hz / 1e6:.0f}-{band.end_hz / 1e6:.0f} MHz)")
        print("Сканирование:", f"{client.start_hz / 1e6:.1f}-{client.end_hz / 1e6:.1f} MHz,",
              client.bins, "бинов")
        got = 0
        t0 = time.time()
        while got < 3 and time.time() - t0 < 10:
            frame = client.read_frame()
            if frame is not None:
                got += 1
                print(f"FRAME ок: {frame.size} бинов, min={frame.min()} dBm, max={frame.max()} dBm")
            time.sleep(0.02)
        return 0 if got else 3
    except (AirViewError, DataSourceError) as exc:  # type: ignore[attr-defined]
        print(f"ОШИБКА: {exc}")
        return 1
    finally:
        try:
            client.shutdown()
        except Exception:  # noqa: BLE001
            pass


def cmd_headless(settings: Settings) -> int:
    """Без GUI: читает кадры, пишет .dat, печатает статистику."""
    src = make_source(settings)
    frames = 0
    t0 = time.time()
    last_report = t0
    try:
        if isinstance(src, AirViewClient):
            attach = src.probe_running() and settings.attach_policy == "attach"
            src.start(attach=attach)
        else:
            src.start()

        while True:
            frame = src.read_frame()
            if frame is None:
                time.sleep(max(0.001, settings.poll_interval_ms / 1000.0))
                continue
            frames += 1
            if settings.headless_frames and frames >= settings.headless_frames:
                print(f"\nпрочитано {frames} кадров")
                break
            now = time.time()
            if now - last_report >= 1.0:
                last_report = now
                fps = frames / max(1e-6, now - t0)
                print(
                    f"\rframes={frames} fps={fps:5.1f} bins={frame.size} "
                    f"min={frame.min():4d} max={frame.max():4d} dBm",
                    end="", flush=True,
                )
    except KeyboardInterrupt:
        print("\nостановлено пользователем")
    except (AirViewError, DataSourceError) as exc:  # type: ignore[attr-defined]
        print(f"\nОШИБКА: {exc}")
        return 1
    finally:
        try:
            if isinstance(src, AirViewClient):
                src.shutdown()
            else:
                src.stop()
        except Exception:  # noqa: BLE001
            pass
    print()
    return 0


def cmd_gui(settings: Settings) -> int:
    """Единственный QApplication на процесс; при повторном вызове возвращает существующий."""
    from PySide6.QtWidgets import QApplication

    from .ui import MainWindow

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    app.setApplicationName("airView Analyzer")
    app.setApplicationVersion(__version__)

    window = MainWindow(settings)
    window.show()
    window.raise_()
    return app.exec()


def main(argv: Optional[list[str]] = None) -> int:
    settings, args = load_from_cli(argv)
    if args.headless:
        return cmd_headless(settings)
    if args.check:
        return cmd_check(settings)
    return cmd_gui(settings)


if __name__ == "__main__":
    sys.exit(main())
