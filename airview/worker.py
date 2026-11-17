"""Фоновый поток: читает кадры из источника и отдаёт их в GUI через сигналы.

Поток не падает на ошибках сети: сообщает статус, пытается переподключиться,
и только после исчерпания попыток завершает работу с сигналом failed.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

import numpy as np
from PySide6.QtCore import QThread, Signal

from .airview_client import AirViewClient, AirViewError
from .config import Settings
from .datasource import DataSource, DataSourceError, MockDataSource, ReplayDataSource


class SpectrumWorker(QThread):
    """Читает кадры в отдельном потоке."""

    frame_ready = Signal(object)          # np.ndarray int16
    status_changed = Signal(str, str)     # текст, уровень (info|warn|error)
    device_info = Signal(object)          # DeviceConfig
    stats_changed = Signal(dict)          # fps/кадры/состояние
    failed = Signal(str)                  # фатальная ошибка, поток завершён

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._stop_flag = threading.Event()
        self._source: Optional[DataSource] = None
        self._pending_range: Optional[tuple[int, int]] = None
        self._range_lock = threading.Lock()

    # ------------------------------------------------------------------ API
    def request_range(self, start_hz: int, end_hz: int) -> None:
        """Потокобезопасно просит сменить диапазон сканирования."""
        with self._range_lock:
            self._pending_range = (int(start_hz), int(end_hz))

    def request_stop(self) -> None:
        self._stop_flag.set()

    def _take_pending_range(self) -> Optional[tuple[int, int]]:
        with self._range_lock:
            rng = self._pending_range
            self._pending_range = None
        return rng

    # ------------------------------------------------------------------ поток
    def run(self) -> None:
        src = self._make_source()
        if src is None:
            return
        self._source = src

        try:
            self._open(src)
        except DataSourceError as exc:
            self.status_changed.emit(str(exc), "error")
            self.failed.emit(str(exc))
            return

        frame_count = 0
        prev_count = 0
        stats_ts = time.monotonic()
        try:
            while not self._stop_flag.is_set():
                # смена диапазона по запросу из UI
                rng = self._take_pending_range()
                if rng is not None and isinstance(src, AirViewClient):
                    try:
                        src.set_range(*rng)
                        self.status_changed.emit(
                            f"Диапазон переключён: {rng[0] / 1e6:.1f}–{rng[1] / 1e6:.1f} МГц", "info"
                        )
                    except DataSourceError as exc:
                        self.status_changed.emit(f"не удалось сменить диапазон: {exc}", "warn")

                try:
                    frame = src.read_frame()
                except DataSourceError as exc:
                    if self._stop_flag.is_set():
                        break
                    self.status_changed.emit(f"ошибка чтения: {exc}", "warn")
                    if not self._reconnect(src):
                        self.failed.emit(f"соединение потеряно: {exc}")
                        break
                    continue

                if frame is not None and frame.size:
                    frame_count += 1
                    self.frame_ready.emit(frame)
                else:
                    time.sleep(max(0.001, self.settings.poll_interval_ms / 1000.0))

                now = time.monotonic()
                if now - stats_ts >= 1.0:
                    window = now - stats_ts
                    fps = (frame_count - prev_count) / window if window > 0 else 0.0
                    prev_count = frame_count
                    stats_ts = now
                    self.stats_changed.emit(
                        {
                            "fps": round(fps, 1),
                            "frames": src.frames_received,
                            "state": "live",
                        }
                    )
        finally:
            self._close(src)

    # ------------------------------------------------------------------ служебное
    def _make_source(self) -> Optional[DataSource]:
        source = self.settings.source
        if source == "mock":
            return MockDataSource(
                start_mhz=self.settings.mock_start_mhz,
                end_mhz=self.settings.mock_end_mhz,
                bins=self.settings.mock_bins,
            )
        if source == "replay":
            return ReplayDataSource(self.settings.replay_file)
        return AirViewClient(self.settings, status_cb=self._on_client_status, diag_cb=self._on_diag)

    def _on_client_status(self, text: str, level: str) -> None:
        self.status_changed.emit(text, level)

    def _on_diag(self, direction: str, payload: bytes) -> None:
        # диагностический hex-дамп не спамим в статус-бар
        pass

    def _open(self, src: DataSource) -> None:
        if isinstance(src, AirViewClient):
            attach = False
            if self.settings.attach_policy == "attach":
                attach = src.probe_running()
            elif self.settings.attach_policy == "restart" and src.probe_running():
                self.status_changed.emit("Перезапускаю запущенный AirView…", "warn")
                try:
                    src.login()
                    src.stop_airview()
                    time.sleep(3.0)
                except DataSourceError:
                    pass
            src.start(attach=attach)
            if src.device is not None:
                self.device_info.emit(src.device)
            self.status_changed.emit(
                f"Подключено: {self.settings.host}:{self.settings.port}", "info"
            )
        else:
            src.start()
            self.status_changed.emit("Источник открыт", "info")
            if src.device is not None:
                self.device_info.emit(src.device)

    def _close(self, src: DataSource) -> None:
        try:
            if isinstance(src, AirViewClient):
                src.shutdown()
            else:
                src.stop()
        except Exception:  # noqa: BLE001 — при выходе не падаем
            pass

    def _reconnect(self, src: DataSource) -> bool:
        """Переподключение с backoff. Возвращает False, если попытки исчерпаны."""
        if not isinstance(src, AirViewClient):
            return False
        attempts = max(1, int(self.settings.reconnect_attempts))
        for attempt in range(1, attempts + 1):
            if self._stop_flag.is_set():
                return False
            delay = self.settings.reconnect_backoff_s * attempt
            self.status_changed.emit(
                f"Переподключение {attempt}/{attempts} через {delay:.1f} с…", "warn"
            )
            time.sleep(delay)
            if self._stop_flag.is_set():
                return False
            try:
                src.stop()
                src.start(attach=False)
                if src.device is not None:
                    self.device_info.emit(src.device)
                self.status_changed.emit("Переподключено", "info")
                return True
            except DataSourceError as exc:
                self.status_changed.emit(f"попытка {attempt} не удалась: {exc}", "warn")
        return False
