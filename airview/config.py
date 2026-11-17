"""Настройки приложения: JSON-файл, значения по умолчанию, командная строка.

Порядок приоритетов: значения по умолчанию < JSON-файл < аргументы CLI.
Пароль попадает в JSON только при включённом `remember_password`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

SETTINGS_NAME = "airview_settings.json"


def app_dir() -> Path:
    """Каталог рядом с исполняемым файлом (для PyInstaller — папка exe)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def default_settings_path() -> Path:
    return app_dir() / SETTINGS_NAME


@dataclass
class Settings:
    """Все параметры приложения (всё, что можно менять из UI/CLI/JSON)."""

    # --- подключение ------------------------------------------------------
    host: str = "192.168.1.20"
    port: int = 18888
    username: str = "manager"
    password: str = ""
    remember_password: bool = False

    # --- что показывать ---------------------------------------------------
    source: str = "device"          # device | mock | replay
    replay_file: str = ""

    # --- диапазон ---------------------------------------------------------
    band_index: int = -1            # -1 = весь диапазон устройства, иначе индекс пресета
    use_custom_range: bool = False
    custom_start_mhz: int = 5150
    custom_end_mhz: int = 5850

    # --- таймауты и частоты ----------------------------------------------
    http_timeout_s: float = 10.0
    web_retries: int = 3            # попыток достучаться до веб-интерфейса (точка перезагружает радио)
    web_retry_delay_s: float = 5.0
    connect_timeout_s: float = 5.0
    io_timeout_s: float = 5.0
    launch_timeout_s: float = 40.0   # ожидание открытия порта 18888 после Launch
    poll_interval_ms: int = 15       # пауза между опросами GET FRAME
    reconnect_attempts: int = 3
    reconnect_backoff_s: float = 1.0
    stop_airview_on_exit: bool = True
    attach_policy: str = "ask"       # ask | attach | restart

    # --- дампы и диагностика ---------------------------------------------
    dump_enabled: bool = True
    dump_dir: str = "airview_dumps"
    record_file: str = ""            # явный путь .dat (--record), иначе имя по таймстампу
    diagnostics: bool = False
    diag_bytes: int = 256

    # --- отображение ------------------------------------------------------
    display_fps: int = 25
    waterfall_rows: int = 400
    mock_bins: int = 1024
    mock_start_mhz: int = 5150
    mock_end_mhz: int = 5850
    theme: str = "dark"

    headless: bool = False
    headless_frames: int = 0        # 0 = без ограничения (только Ctrl+C)

    # --- служебное --------------------------------------------------------
    autosave: bool = True

    # =====================================================================
    def to_dict(self) -> dict:
        data = asdict(self)
        if not self.remember_password:
            data["password"] = ""
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        clean = {k: v for k, v in data.items() if k in known}
        return cls(**clean)

    def copy(self) -> "Settings":
        return Settings.from_dict(self.to_dict())

    # ------------------------------------------------------------------
    def dump_path_for(self, when: Optional[float] = None) -> Path:
        base = Path(self.dump_dir)
        if not base.is_absolute():
            base = app_dir() / base
        base.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(when))
        return base / f"airview-{stamp}.dat"

    def resolve_range(self, device) -> tuple[int, int]:
        """Выбирает диапазон сканирования: custom → пресет → весь диапазон."""
        if device is None:
            return self.mock_start_mhz * 1_000_000, self.mock_end_mhz * 1_000_000

        lo = int(device.min_freq_mhz) * 1_000_000
        hi = int(device.max_freq_mhz) * 1_000_000
        if lo <= 0 or hi <= lo:
            return 0, 0

        if self.use_custom_range:
            start = int(self.custom_start_mhz) * 1_000_000
            end = int(self.custom_end_mhz) * 1_000_000
            start = min(max(start, lo), hi)
            end = min(max(end, lo), hi)
            if end - start >= int(device.bin_hz):
                return start, end

        if 0 <= self.band_index < len(device.bands):
            band = device.bands[self.band_index]
            return band.start_hz, band.end_hz

        return lo, hi


# ---------------------------------------------------------------------- JSON
def load_settings(path: Path) -> Settings:
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Settings.from_dict(data)
        except (OSError, ValueError):
            pass
    return Settings()


def save_settings(settings: Settings, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(settings.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="airview",
        description="airView Analyzer — спектроанализатор для airOS 6.x (порт 18888)",
    )
    p.add_argument("--config", help="путь к JSON-файлу настроек")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--user", dest="username")
    p.add_argument("--password")
    p.add_argument("--remember-password", action="store_true", default=None)
    p.add_argument("--source", choices=["device", "mock", "replay"])
    p.add_argument("--replay", dest="replay_file", metavar="FILE")
    p.add_argument("--mock", action="store_true", help="режим имитации (без устройства)")
    p.add_argument("--band", type=int, help="индекс пресета диапазона (-1 = весь диапазон)")
    p.add_argument("--start-mhz", type=int, dest="custom_start_mhz")
    p.add_argument("--end-mhz", type=int, dest="custom_end_mhz")
    p.add_argument("--http-timeout", type=float, dest="http_timeout_s")
    p.add_argument("--connect-timeout", type=float, dest="connect_timeout_s")
    p.add_argument("--io-timeout", type=float, dest="io_timeout_s")
    p.add_argument("--launch-timeout", type=float, dest="launch_timeout_s")
    p.add_argument("--poll-ms", type=int, dest="poll_interval_ms")
    p.add_argument("--reconnect", type=int, dest="reconnect_attempts")
    p.add_argument("--backoff", type=float, dest="reconnect_backoff_s")
    p.add_argument("--dumps-dir", dest="dump_dir")
    p.add_argument("--no-dumps", action="store_true", default=None)
    p.add_argument("--diagnostics", action="store_true", default=None)
    p.add_argument("--attach", action="store_true", default=None,
                   help="подключиться к уже запущенному AirView, не перезапускать")
    p.add_argument("--restart", action="store_true", help="принудительно перезапустить AirView")
    p.add_argument("--no-stop", action="store_true", default=None,
                   help="не выключать AirView при выходе")
    p.add_argument("--headless", action="store_true", help="без GUI: читать кадры и писать дамп")
    p.add_argument("--frames", type=int, dest="headless_frames",
                   help="headless: остановиться после N кадров")
    p.add_argument("--record", metavar="FILE", help="headless: имя .dat-файла для записи")
    p.add_argument("--check", action="store_true", help="подключиться, показать CONFIGURATION и выйти")
    p.add_argument("--fps", type=int, dest="display_fps")
    return p


def merge_cli(settings: Settings, args: argparse.Namespace) -> Settings:
    """Накладывает аргументы CLI поверх настроек."""
    s = settings.copy()
    if args.host is not None:
        s.host = args.host
    if args.port is not None:
        s.port = args.port
    if args.username is not None:
        s.username = args.username
    if args.password is not None:
        s.password = args.password
    if args.remember_password:
        s.remember_password = True
    if args.source:
        s.source = args.source
    if args.replay_file:
        s.source = "replay"
        s.replay_file = args.replay_file
    if args.mock:
        s.source = "mock"
    if args.band is not None:
        s.band_index = args.band
        s.use_custom_range = False
    if args.custom_start_mhz is not None or args.custom_end_mhz is not None:
        s.use_custom_range = True
        if args.custom_start_mhz is not None:
            s.custom_start_mhz = args.custom_start_mhz
        if args.custom_end_mhz is not None:
            s.custom_end_mhz = args.custom_end_mhz
    for attr in (
        "http_timeout_s", "connect_timeout_s", "io_timeout_s", "launch_timeout_s",
        "poll_interval_ms", "reconnect_attempts", "reconnect_backoff_s", "dump_dir",
        "display_fps",
    ):
        value = getattr(args, attr, None)
        if value is not None:
            setattr(s, attr, value)
    if args.no_dumps:
        s.dump_enabled = False
    if args.diagnostics:
        s.diagnostics = True
    if args.attach:
        s.attach_policy = "attach"
    if args.restart:
        s.attach_policy = "restart"
    if args.no_stop:
        s.stop_airview_on_exit = False
    if args.record:
        s.dump_enabled = True
        s.record_file = str(Path(args.record).resolve())
        s.dump_dir = str(Path(args.record).resolve().parent)
    if args.headless_frames:
        s.headless_frames = int(args.headless_frames)
    return s


def load_from_cli(argv: Optional[list[str]] = None) -> tuple[Settings, argparse.Namespace]:
    parser = build_parser()
    args = parser.parse_args(argv)
    path = Path(args.config) if args.config else default_settings_path()
    settings = load_settings(path)
    settings = merge_cli(settings, args)
    return settings, args
