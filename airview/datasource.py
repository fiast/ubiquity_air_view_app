"""Источники данных для спектроанализатора.

DataSource — общий интерфейс; MockDataSource — генератор спектра для отладки;
ReplayDataSource — воспроизведение записанного .dat-дампа (формат совместим с airviewer.py).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import numpy as np

from .protocol import DeviceConfig, parse_frame, parse_scan_range


class DataSourceError(RuntimeError):
    """Ошибка источника данных (сеть, протокол, таймаут)."""


class DataSource(ABC):
    """Абстрактный источник кадров спектра."""

    name = "abstract"

    def __init__(self) -> None:
        self.device: Optional[DeviceConfig] = None
        self.start_hz: int = 0
        self.end_hz: int = 0
        self.last_frame_nr: int = -1
        self.frames_received: int = 0

    # --- жизненный цикл ---------------------------------------------------
    @abstractmethod
    def start(self) -> None:
        """Открыть источник (сеть/файл)."""

    @abstractmethod
    def stop(self) -> None:
        """Закрыть источник, освободить ресурсы."""

    # --- чтение -----------------------------------------------------------
    @abstractmethod
    def read_frame(self) -> Optional[np.ndarray]:
        """Вернуть один новый кадр (дБм) или None, если нового кадра нет."""

    # --- управление -------------------------------------------------------
    def set_range(self, start_hz: int, end_hz: int) -> None:
        """Сменить диапазон сканирования (источники без поддержки игнорируют)."""

    @property
    def bins(self) -> int:
        if self.device is not None and self.start_hz and self.end_hz:
            return self.device.bins_for(self.start_hz, self.end_hz)
        return 0

    def frequencies_mhz(self) -> np.ndarray:
        """Центры бинов, МГц — для оси X."""
        if not self.start_hz or not self.end_hz:
            return np.zeros(0, dtype=np.float32)
        bins = max(1, self.bins)
        edges = np.linspace(self.start_hz, self.end_hz, bins + 1, dtype=np.float64)
        centers = (edges[:-1] + edges[1:]) / 2.0
        return (centers / 1e6).astype(np.float32)


# ---------------------------------------------------------------------- Mock
class MockDataSource(DataSource):
    """Синтетический спектр: шумовая полка + случайные пики (для отладки UI)."""

    name = "mock"

    def __init__(
        self,
        start_mhz: int = 5150,
        end_mhz: int = 5850,
        bins: int = 1024,
        noise_floor_dbm: float = -95.0,
        frame_rate_hz: float = 10.0,
        seed: Optional[int] = None,
    ) -> None:
        super().__init__()
        self.start_hz = start_mhz * 1_000_000
        self.end_hz = end_mhz * 1_000_000
        self._bins = max(8, bins)
        self.noise_floor = noise_floor_dbm
        self.frame_period = 1.0 / max(0.1, frame_rate_hz)
        self.rng = np.random.default_rng(seed)
        self._t0 = 0.0
        self._last = 0.0
        # несколько медленно дрейфующих пиков
        self._peaks = [
            (self.rng.uniform(0.1, 0.9), self.rng.uniform(-65, -40), self.rng.uniform(0.004, 0.02), self.rng.uniform(0.0, 6.28))
            for _ in range(6)
        ]

    @property
    def bins(self) -> int:  # type: ignore[override]
        return self._bins

    def start(self) -> None:
        self._t0 = time.monotonic()
        self._last = 0.0

    def stop(self) -> None:
        pass

    def read_frame(self) -> Optional[np.ndarray]:
        now = time.monotonic() - self._t0
        if now - self._last < self.frame_period:
            time.sleep(max(0.0, self.frame_period - (now - self._last)))
            now = time.monotonic() - self._t0
        self._last = now
        x = np.linspace(0.0, 1.0, self._bins, dtype=np.float32)
        floor = self.noise_floor + self.rng.normal(0.0, 0.8, self._bins).astype(np.float32)
        spec = floor.astype(np.float32)
        for center, amp, width, phase in self._peaks:
            c = center + 0.02 * np.sin(now * 0.3 + phase)
            bump = (amp - self.noise_floor) * np.exp(-0.5 * ((x - c) / width) ** 2)
            spec = np.maximum(spec, self.noise_floor + bump)
        self.last_frame_nr += 1
        self.frames_received += 1
        return np.clip(spec, -150, 0).astype(np.int16)


# ---------------------------------------------------------------------- Replay
class ReplayDataSource(DataSource):
    """Воспроизведение .dat (строки «таймстамп\\x01строка»), записанного клиентом или airviewer.py."""

    name = "replay"

    def __init__(self, path: str | Path, loop: bool = False, speed: float = 1.0) -> None:
        super().__init__()
        self.path = Path(path)
        self.loop = loop
        self.speed = max(0.01, speed)
        self._lines: list[str] = []
        self._pos = 0
        self._t0 = 0.0
        self._prev_ts: Optional[float] = None

    def start(self) -> None:
        if not self.path.is_file():
            raise DataSourceError(f"файл не найден: {self.path}")
        self._lines = []
        with self.path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.rstrip("\n")
                if not line:
                    continue
                if "\x01" in line:
                    _, payload = line.split("\x01", 1)
                else:
                    payload = line
                payload = payload.strip()
                if payload.upper().startswith(("FRAME:", "SCAN RANGE:")):
                    self._lines.append(payload)
        if not self._lines:
            raise DataSourceError(f"в {self.path} нет строк FRAME/SCAN RANGE")
        self._pos = 0
        self._t0 = time.monotonic()
        self._prev_ts = None

    def stop(self) -> None:
        pass

    def read_frame(self) -> Optional[np.ndarray]:
        if self._pos >= len(self._lines):
            if not self.loop:
                return None
            self._pos = 0
        line = self._lines[self._pos]
        self._pos += 1

        rng = parse_scan_range(line)
        if rng is not None:
            self.start_hz, self.end_hz = rng
            return self.read_frame()

        parsed = parse_frame(line)
        if parsed is None:
            return self.read_frame()

        nr, values = parsed
        self.last_frame_nr = nr
        self.frames_received += 1
        # при воспроизведении держим примерно исходный темп
        delay = 1.0 / max(1.0, 7.5) / self.speed
        if delay > 0:
            time.sleep(delay)
        return values
