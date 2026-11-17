"""Разбор текстового протокола airView (airOS 6.x, порт 18888).

Протокол построчный, ASCII, команды и ответы заканчиваются `\\n`.
Пример обмена (снято с PowerBeam M5 400, XW.v6.3.11):

    >> CONNECT: \\n
    << CONFIGURATION: 2,XW.v6.3.11,Tue Apr 25 16:48:45 EEST 2023,0xe4e5,PowerBeam M5,
       PowerBeam M5 400,24A43CE88865,P5B,...,312500,<каналы>,...,1,dbm,...,-150,4920,6100
    >> REQUEST RANGE: 5150000000,5250000000\\n
    << SCAN RANGE: 5150000000,5250000000\\n
    >> START SCAN: \\n
    << RESULT: 0\\n
    >> GET FRAME: \\n
    << FRAME:  15,-114,-111,-119,...\\n      (дБм, целые числа)

Число бинов = (end_hz - start_hz) / bin_hz (bin_hz приходит в CONFIGURATION, у airOS 6.3 — 312500).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

# ---------------------------------------------------------------- команды
CMD_CONNECT = b"CONNECT: \n"
CMD_START_SCAN = b"START SCAN: \n"
CMD_GET_FRAME = b"GET FRAME: \n"


def cmd_request_range(start_hz: int, end_hz: int) -> bytes:
    """Команда установки диапазона сканирования (частоты в Гц)."""
    return f"REQUEST RANGE: {int(start_hz)},{int(end_hz)}\n".encode("ascii")


# ---------------------------------------------------------------- структуры
@dataclass(frozen=True)
class Band:
    """Предустановленный диапазон сканирования из CONFIGURATION."""

    start_hz: int
    end_hz: int
    label: str

    @property
    def width_mhz(self) -> float:
        return (self.end_hz - self.start_hz) / 1e6


@dataclass(frozen=True)
class Channel:
    """Канал из CONFIGURATION: номер, центральная частота и ширина."""

    number: int
    center_hz: int
    width_hz: int


@dataclass
class DeviceConfig:
    """Разобранный ответ CONFIGURATION — всё, что устройство сообщает о себе."""

    version: str = ""
    build_date: str = ""
    build_id: str = ""
    model: str = ""
    model_full: str = ""
    mac: str = ""
    board: str = ""
    bands: list[Band] = field(default_factory=list)
    bin_hz: int = 312500
    channels: list[Channel] = field(default_factory=list)
    unit: str = "dbm"
    unit_desc: str = ""
    min_dbm: int = -150
    min_freq_mhz: int = 0
    max_freq_mhz: int = 0
    raw_fields: list[str] = field(default_factory=list)

    # --- удобные свойства -------------------------------------------------
    @property
    def full_start_hz(self) -> int:
        return int(self.min_freq_mhz) * 1_000_000

    @property
    def full_end_hz(self) -> int:
        return int(self.max_freq_mhz) * 1_000_000

    def bins_for(self, start_hz: int, end_hz: int) -> int:
        """Сколько бинов вернёт устройство для диапазона."""
        if self.bin_hz <= 0:
            return 0
        return max(1, int(round((end_hz - start_hz) / self.bin_hz)))

    def describe(self) -> str:
        return (
            f"{self.model_full or self.model} | fw {self.version} | "
            f"MAC {self.mac} | {self.min_freq_mhz}-{self.max_freq_mhz} MHz"
        )


# ---------------------------------------------------------------- разбор
def _strip_line(line: bytes | str) -> str:
    if isinstance(line, (bytes, bytearray)):
        line = bytes(line).decode("ascii", "replace")
    return line.strip().strip("\x00").strip()


def parse_configuration(line: bytes | str) -> DeviceConfig:
    """Разбирает строку `CONFIGURATION: ...`.

    Индексы полей зафиксированы по факту (airOS 6.3.11):
        1  — версия прошивки; 2 — дата сборки; 4/5 — модель; 6 — MAC
        13 — список пресетов ("start|end|label" через "~")
        14 — ширина бина в Гц
        15 — каналы ("номер|центр|ширина" через "~")
        18/19 — единицы измерения
        21 — минимальный уровень дБм; 22/23 — минимальная/максимальная частота, МГц
    """
    text = _strip_line(line)
    if ":" in text:
        text = text.split(":", 1)[1]
    fields = text.split(",")

    def at(idx: int, default: str = "") -> str:
        return fields[idx] if idx < len(fields) else default

    cfg = DeviceConfig(raw_fields=fields)
    cfg.version = at(1)
    cfg.build_date = at(2)
    cfg.build_id = at(3)
    cfg.model = at(4)
    cfg.model_full = at(5)
    cfg.mac = at(6)
    cfg.board = at(7)

    # 13: пресеты диапазонов
    for entry in at(13).split("~"):
        if not entry:
            continue
        parts = entry.split("|")
        if len(parts) >= 2:
            try:
                band = Band(int(parts[0]), int(parts[1]), parts[2] if len(parts) > 2 else "")
            except ValueError:
                continue
            cfg.bands.append(band)

    try:
        cfg.bin_hz = int(at(14))
    except ValueError:
        cfg.bin_hz = 312500

    # 15: каналы
    for entry in at(15).split("~"):
        if not entry:
            continue
        parts = entry.split("|")
        if len(parts) >= 3:
            try:
                ch = Channel(int(parts[0]), int(parts[1]), int(parts[2]))
            except ValueError:
                continue
            cfg.channels.append(ch)

    cfg.unit = at(18, "dbm")
    cfg.unit_desc = at(19)
    try:
        cfg.min_dbm = int(at(21))
    except ValueError:
        cfg.min_dbm = -150
    try:
        cfg.min_freq_mhz = int(at(22))
        cfg.max_freq_mhz = int(at(23))
    except ValueError:
        pass
    return cfg


def parse_frame(line: bytes | str) -> Optional[tuple[int, np.ndarray]]:
    """Разбирает строку `FRAME: <номер>,<v1>,<v2>,...` в (номер, np.int16-массив дБм)."""
    text = _strip_line(line)
    if not text.upper().startswith("FRAME:"):
        return None
    payload = text.split(":", 1)[1]
    parts = payload.split(",")
    if len(parts) < 2:
        return None
    try:
        frame_nr = int(parts[0])
        values = np.fromiter((int(v) for v in parts[1:] if v.strip()), dtype=np.int16)
    except ValueError:
        return None
    if values.size == 0:
        return None
    return frame_nr, values


def parse_scan_range(line: bytes | str) -> Optional[tuple[int, int]]:
    """Разбирает строку `SCAN RANGE: <start_hz>,<end_hz>`."""
    text = _strip_line(line)
    if not text.upper().startswith("SCAN RANGE:"):
        return None
    try:
        start, end = text.split(":", 1)[1].split(",")
        return int(start), int(end)
    except ValueError:
        return None


def parse_result(line: bytes | str) -> Optional[int]:
    """Разбирает строку `RESULT: <код>` (0 — успех)."""
    text = _strip_line(line)
    if not text.upper().startswith("RESULT:"):
        return None
    try:
        return int(text.split(":", 1)[1])
    except ValueError:
        return None


def hexdump(data: bytes, limit: int = 256) -> str:
    """Короткий hex-дамп для диагностики."""
    chunk = data[:limit]
    hexpart = chunk.hex(" ")
    text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
    return f"{len(data)}B | {hexpart} | {text}"
