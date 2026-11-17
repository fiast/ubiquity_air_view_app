"""Главное окно airView Analyzer: спектр (Live / Max Hold / Average) и водопад.

Всё рисование — PyQtGraph; кадры приходят из SpectrumWorker отдельным потоком.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .airview_client import AirViewClient
from .config import Settings, default_settings_path, load_settings, save_settings
from .datasource import DataSource
from .worker import SpectrumWorker

pg.setConfigOptions(antialias=True, background="#0b0b0b", foreground="#d0d0d0")

CUSTOM_ITEM = "Custom range…"
CUSTOM_ID = -2
FULL_ITEM = "Full range"
FULL_ID = -1
COLOR_LIVE = "#22d3ee"
COLOR_MAX = "#f87171"
COLOR_AVG = "#fbbf24"


class MainWindow(QMainWindow):
    """Окно анализатора."""

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self._worker: Optional[SpectrumWorker] = None
        self._bins = 0
        self._mid_mhz = np.zeros(0, dtype=np.float32)
        self._max = np.zeros(0, dtype=np.float32)
        self._avg = np.zeros(0, dtype=np.float32)
        self._waterfall: Optional[np.ndarray] = None
        self._wf_rect: Optional[QRectF] = None
        self._device = None
        self._band_items: list[tuple[str, int, int]] = []
        self._applied_device = False

        self.setWindowTitle(f"airView Analyzer {__version__}")
        self.resize(1280, 820)

        self._build_ui()
        self._restore_from_settings()

        # mock/replay удобно показывать сразу, без нажатия Connect
        if self.settings.source in ("mock", "replay"):
            QTimer.singleShot(0, self._on_connect)

    # ================================================================= UI
    def _build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        layout.addWidget(self._build_controls())
        layout.addWidget(self._build_plots(), 1)

        self.setCentralWidget(central)
        self._build_statusbar()

    def _build_controls(self) -> QGroupBox:
        box = QGroupBox("Connection")
        grid = QGridLayout(box)

        self.host_edit = QLineEdit()
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.user_edit = QLineEdit()
        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.remember_check = QCheckBox("Remember password")

        self.source_combo = QComboBox()
        self.source_combo.addItem("Device (TCP)", "device")
        self.source_combo.addItem("Mock (demo)", "mock")
        self.source_combo.addItem("Replay (.dat)", "replay")

        self.band_combo = QComboBox()
        self.band_combo.addItem(FULL_ITEM, FULL_ID)
        self.band_combo.addItem(CUSTOM_ITEM, CUSTOM_ID)
        self.start_spin = QDoubleSpinBox()
        self.start_spin.setRange(100.0, 12000.0)
        self.start_spin.setDecimals(1)
        self.start_spin.setSuffix(" MHz")
        self.end_spin = QDoubleSpinBox()
        self.end_spin.setRange(100.0, 12000.0)
        self.end_spin.setDecimals(1)
        self.end_spin.setSuffix(" MHz")

        self.dump_check = QCheckBox("Dump .dat")
        self.diag_check = QCheckBox("Hex diagnostics")

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setDefault(True)
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.setEnabled(False)
        self.save_btn = QPushButton("Save settings")
        self.load_btn = QPushButton("Load settings")
        self.replay_btn = QPushButton("Replay file…")

        row = 0
        grid.addWidget(QLabel("Host"), row, 0)
        grid.addWidget(self.host_edit, row, 1)
        grid.addWidget(QLabel("Port"), row, 2)
        grid.addWidget(self.port_spin, row, 3)
        grid.addWidget(QLabel("Login"), row, 4)
        grid.addWidget(self.user_edit, row, 5)
        grid.addWidget(QLabel("Password"), row, 6)
        grid.addWidget(self.pass_edit, row, 7)
        grid.addWidget(self.remember_check, row, 8)

        row += 1
        grid.addWidget(QLabel("Source"), row, 0)
        grid.addWidget(self.source_combo, row, 1)
        grid.addWidget(QLabel("Band"), row, 2, 1, 1)
        grid.addWidget(self.band_combo, row, 3, 1, 3)
        grid.addWidget(self.start_spin, row, 6)
        grid.addWidget(self.end_spin, row, 7)

        row += 1
        grid.addWidget(self.dump_check, row, 0, 1, 2)
        grid.addWidget(self.diag_check, row, 2, 1, 2)
        grid.addWidget(self.connect_btn, row, 5)
        grid.addWidget(self.disconnect_btn, row, 6)
        grid.addWidget(self.replay_btn, row, 7)
        grid.addWidget(self.load_btn, row, 8)
        grid.addWidget(self.save_btn, row, 9)

        self.connect_btn.clicked.connect(self._on_connect)
        self.disconnect_btn.clicked.connect(self._on_disconnect)
        self.save_btn.clicked.connect(self._on_save)
        self.load_btn.clicked.connect(self._on_load)
        self.replay_btn.clicked.connect(self._on_pick_replay)
        self.band_combo.currentIndexChanged.connect(self._on_band_changed)
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        self.start_spin.valueChanged.connect(self._on_custom_range_changed)
        self.end_spin.valueChanged.connect(self._on_custom_range_changed)
        return box

    def _build_plots(self) -> QWidget:
        widget = QWidget()
        lay = QVBoxLayout(widget)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        self.graphics = pg.GraphicsLayoutWidget()
        lay.addWidget(self.graphics, 3)

        # --- спектр --------------------------------------------------------
        self.spec_plot = self.graphics.addPlot(row=0, col=0)
        self.spec_plot.setLabel("left", "Power", units="dBm")
        self.spec_plot.setLabel("bottom", "Frequency", units="MHz")
        self.spec_plot.showGrid(x=True, y=True, alpha=0.25)
        self.spec_plot.setYRange(-150, -20)
        self.spec_plot.addLegend(offset=(-10, 10))
        self.spec_plot.setMenuEnabled(False)

        self.curve_live = self.spec_plot.plot(pen=pg.mkPen(COLOR_LIVE, width=1.4), name="Live")
        self.curve_max = self.spec_plot.plot(pen=pg.mkPen(COLOR_MAX, width=1.0), name="Max Hold")
        self.curve_avg = self.spec_plot.plot(pen=pg.mkPen(COLOR_AVG, width=1.0), name="Average")

        # --- водопад -------------------------------------------------------
        self.wf_plot = pg.PlotItem()
        self.wf_plot.setLabel("left", "Time")
        self.wf_plot.setLabel("bottom", "Frequency", units="MHz")
        self.wf_plot.showGrid(x=True, y=False, alpha=0.2)
        self.wf_plot.setMenuEnabled(False)
        self.wf_plot.setYRange(0, max(1, self.settings.waterfall_rows))
        self.wf_plot.getAxis("left").setStyle(showValues=False)

        # собственный «inferno»-подобный colormap, без зависимости от matplotlib/colorcet
        stops = [
            (0.0, (0, 0, 4)),
            (0.25, (87, 16, 110)),
            (0.5, (188, 55, 84)),
            (0.75, (249, 142, 9)),
            (1.0, (252, 255, 164)),
        ]
        cmap = pg.ColorMap([p for p, _ in stops], [c for _, c in stops])
        # row-major: массив водопада задаётся как (строки времени × колонки частот)
        self.wf_image = pg.ImageItem(axisOrder="row-major")
        self.wf_image.setLookupTable(cmap.getLookupTable(0.0, 1.0, 256))
        self.wf_image.setLevels((-140, -45))
        self.wf_plot.addItem(self.wf_image)
        self.graphics.addItem(self.wf_plot, row=1, col=0)

        self.graphics.ci.layout.setRowStretchFactor(0, 3)
        self.graphics.ci.layout.setRowStretchFactor(1, 4)
        self.spec_plot.setXLink(self.wf_plot)

        # водопад: строка 0 (свежая) — сверху
        self.wf_plot.invertY(True)
        return widget

    def _build_statusbar(self) -> None:
        bar = QStatusBar()
        self.setStatusBar(bar)
        self.device_label = QLabel("device: —")
        self.freq_label = QLabel("range: —")
        self.fps_label = QLabel("fps: 0.0 | frames: 0")
        font = QFont()
        font.setPointSize(9)
        for lbl in (self.device_label, self.freq_label, self.fps_label):
            lbl.setFont(font)
        bar.addWidget(self.device_label, 1)
        bar.addPermanentWidget(self.freq_label)
        bar.addPermanentWidget(self.fps_label)

    # ================================================================= настройки
    def _restore_from_settings(self) -> None:
        s = self.settings
        self.host_edit.setText(s.host)
        self.port_spin.setValue(s.port)
        self.user_edit.setText(s.username)
        self.pass_edit.setText(s.password)
        self.remember_check.setChecked(s.remember_password)
        idx = self.source_combo.findData(s.source)
        if idx >= 0:
            self.source_combo.setCurrentIndex(idx)
        self.dump_check.setChecked(s.dump_enabled)
        self.diag_check.setChecked(s.diagnostics)
        self.start_spin.setValue(s.custom_start_mhz)
        self.end_spin.setValue(s.custom_end_mhz)
        if s.use_custom_range:
            idx = self.band_combo.findData(CUSTOM_ID)
            if idx >= 0:
                self.band_combo.setCurrentIndex(idx)
        elif s.band_index >= 0:
            idx = self.band_combo.findData(s.band_index)
            if idx >= 0:
                self.band_combo.setCurrentIndex(idx)
        else:
            self.band_combo.setCurrentIndex(0)
        self._update_range_fields()

    def _collect_settings(self) -> Settings:
        s = self.settings.copy()
        s.host = self.host_edit.text().strip()
        s.port = self.port_spin.value()
        s.username = self.user_edit.text().strip()
        s.password = self.pass_edit.text()
        s.remember_password = self.remember_check.isChecked()
        s.source = self.source_combo.currentData()
        s.dump_enabled = self.dump_check.isChecked()
        s.diagnostics = self.diag_check.isChecked()
        s.custom_start_mhz = int(round(self.start_spin.value()))
        s.custom_end_mhz = int(round(self.end_spin.value()))
        data = self.band_combo.currentData()
        s.use_custom_range = data == CUSTOM_ID
        s.band_index = data if isinstance(data, int) and data >= 0 else -1
        return s

    # ================================================================= обработчики
    def _on_source_changed(self) -> None:
        self._update_range_fields()

    def _update_range_fields(self) -> None:
        is_device = self.source_combo.currentData() != "mock"
        self.band_combo.setEnabled(is_device)
        custom = self.band_combo.currentData() == CUSTOM_ID
        self.start_spin.setEnabled(custom)
        self.end_spin.setEnabled(custom)

    def _selected_range(self) -> Optional[tuple[int, int]]:
        """Диапазон, соответствующий текущему выбору в комбобоксе (в Гц)."""
        if self._device is None:
            return None
        sel = self.band_combo.currentData()
        if sel == CUSTOM_ID:
            start = int(self.start_spin.value() * 1e6)
            end = int(self.end_spin.value() * 1e6)
            return start, end
        if isinstance(sel, int) and 0 <= sel < len(self._device.bands):
            band = self._device.bands[sel]
            return band.start_hz, band.end_hz
        return self._device.full_start_hz, self._device.full_end_hz

    def _on_band_changed(self) -> None:
        self._update_range_fields()
        if self._worker is None:
            self.settings = self._collect_settings()
            return
        rng = self._selected_range()
        if rng is not None and rng[1] > rng[0]:
            self._worker.request_range(*rng)
            self.statusBar().showMessage(
                f"переключаю диапазон: {rng[0] / 1e6:.1f}–{rng[1] / 1e6:.1f} МГц", 5000
            )

    def _on_custom_range_changed(self) -> None:
        if self._worker is None:
            return
        rng = self._selected_range()
        if rng is not None and rng[1] > rng[0]:
            self._worker.request_range(*rng)

    def _on_pick_replay(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open .dat dump", "", "airView dumps (*.dat);;All files (*)")
        if not path:
            return
        idx = self.source_combo.findData("replay")
        self.source_combo.setCurrentIndex(idx)
        self.settings.replay_file = path
        self.statusBar().showMessage(f"replay: {path}")
        self._on_connect()

    def _on_save(self) -> None:
        self.settings = self._collect_settings()
        path = default_settings_path()
        save_settings(self.settings, path)
        self.statusBar().showMessage(f"saved: {path}", 5000)

    def _on_load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load settings", str(default_settings_path()), "JSON (*.json)")
        if not path:
            return
        self.settings = load_settings(Path(path))
        self._restore_from_settings()
        self.statusBar().showMessage(f"loaded: {path}", 5000)

    # ================================================================= подключение
    def _on_connect(self) -> None:
        if self._worker is not None:
            return
        settings = self._collect_settings()

        if settings.source == "device":
            probe = AirViewClient(settings)
            if probe.probe_running():
                answer = self._ask_running(settings.host, settings.port)
                if answer == "cancel":
                    return
                settings.attach_policy = answer
            else:
                settings.attach_policy = "restart"
        self.settings = settings

        self._clear_spectrum()
        self._worker = SpectrumWorker(settings, self)
        self._worker.frame_ready.connect(self._on_frame)
        self._worker.status_changed.connect(self._on_status)
        self._worker.device_info.connect(self._on_device)
        self._worker.stats_changed.connect(self._on_stats)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)
        self.statusBar().showMessage("connecting…")

    def _ask_running(self, host: str, port: int) -> str:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("airView is already running")
        box.setText(f"On {host}:{port} airView mode is already active.\nWhat should I do?")
        attach = box.addButton("Attach", QMessageBox.ButtonRole.AcceptRole)
        restart = box.addButton("Stop and restart", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is attach:
            return "attach"
        if clicked is restart:
            return "restart"
        return "cancel"

    def _on_disconnect(self) -> None:
        if self._worker is not None:
            self._worker.request_stop()

    def _on_worker_finished(self) -> None:
        if self._worker is not None:
            self._worker.wait(3000)
            self._worker.deleteLater()
        self._worker = None
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        self.statusBar().showMessage("disconnected")

    def _on_failed(self, message: str) -> None:
        self.statusBar().showMessage(f"error: {message}", 10000)
        self._on_worker_finished()

    # ================================================================= данные
    def _on_device(self, device) -> None:
        self._device = device
        self.device_label.setText(f"device: {device.describe()}")
        self.setWindowTitle(
            f"airView Analyzer — {device.model_full or device.model} — "
            f"fw {device.version} — {self.settings.host}"
        )
        self._populate_bands(device)

    def _populate_bands(self, device) -> None:
        self.band_combo.blockSignals(True)
        self.band_combo.clear()
        self.band_combo.addItem(FULL_ITEM, -1)
        self._band_items = []
        for i, band in enumerate(device.bands):
            label = f"{band.label} ({band.start_hz / 1e6:.0f}–{band.end_hz / 1e6:.0f} MHz)"
            self.band_combo.addItem(label, i)
            self._band_items.append((label, band.start_hz, band.end_hz))
        self.band_combo.addItem(CUSTOM_ITEM, -2)

        saved = self.settings.band_index
        if self.settings.use_custom_range:
            self.band_combo.setCurrentIndex(self.band_combo.count() - 1)
        elif 0 <= saved < len(device.bands):
            self.band_combo.setCurrentIndex(saved + 1)
        else:
            self.band_combo.setCurrentIndex(0)
        self.band_combo.blockSignals(False)
        self._update_range_fields()

    def _ensure_buffers(self, bins: int, start_hz: int, end_hz: int) -> None:
        if bins == self._bins and self._mid_mhz.size == bins:
            return
        self._bins = bins
        if start_hz and end_hz and bins:
            edges = np.linspace(start_hz, end_hz, bins + 1, dtype=np.float64)
            self._mid_mhz = ((edges[:-1] + edges[1:]) / 2.0 / 1e6).astype(np.float32)
        else:
            self._mid_mhz = np.arange(bins, dtype=np.float32)
        self._max = np.full(bins, -150.0, dtype=np.float32)
        self._avg = np.full(bins, -150.0, dtype=np.float32)
        rows = max(50, int(self.settings.waterfall_rows))
        self._waterfall = np.full((rows, bins), -150.0, dtype=np.float32)

        self.spec_plot.setXRange(float(self._mid_mhz[0]), float(self._mid_mhz[-1]), padding=0.01)
        self.wf_plot.setXRange(float(self._mid_mhz[0]), float(self._mid_mhz[-1]), padding=0.01)

        # привязываем картинку водопада к реальным частотам
        # (setImage до setRect: pyqtgraph масштабирует по размеру уже загруженной картинки)
        self.wf_image.setImage(self._waterfall, autoLevels=False)
        if start_hz and end_hz and bins:
            step_mhz = (end_hz - start_hz) / bins / 1e6
            self._wf_rect = QRectF(
                float(self._mid_mhz[0]) - step_mhz / 2, 0.0, step_mhz * bins, float(rows)
            )
            self.wf_image.setRect(self._wf_rect)
        self.wf_plot.setYRange(0, rows)
        self.freq_label.setText(
            f"range: {start_hz / 1e6:.1f}–{end_hz / 1e6:.1f} MHz | bins: {bins} | "
            f"step: {(end_hz - start_hz) / bins / 1e3:.1f} kHz"
        )

    def _clear_spectrum(self) -> None:
        self._bins = 0
        self._max = np.zeros(0, dtype=np.float32)
        self._avg = np.zeros(0, dtype=np.float32)
        self._waterfall = None
        self._wf_rect = None
        self.curve_live.setData([])
        self.curve_max.setData([])
        self.curve_avg.setData([])
        self.wf_image.clear()

    def _on_frame(self, frame: np.ndarray) -> None:
        values = frame.astype(np.float32)
        bins = values.size

        worker = self._worker
        src = getattr(worker, "_source", None) if worker is not None else None
        start_hz = int(src.start_hz) if src is not None else 0
        end_hz = int(src.end_hz) if src is not None else 0
        self._ensure_buffers(bins, start_hz, end_hz)

        self._max = np.maximum(self._max, values)
        self._avg = self._avg * 0.85 + values * 0.15 if self._avg.size == bins else values.copy()

        self.curve_live.setData(self._mid_mhz, values)
        self.curve_max.setData(self._mid_mhz, self._max)
        self.curve_avg.setData(self._mid_mhz, self._avg)

        if self._waterfall is not None:
            self._waterfall = np.roll(self._waterfall, 1, axis=0)
            self._waterfall[0] = values
            self.wf_image.setImage(self._waterfall, autoLevels=False)
            # setImage сбрасывает трансформацию — возвращаем привязку к частотам
            if self._wf_rect is not None:
                self.wf_image.setRect(self._wf_rect)

    def _on_stats(self, stats: dict) -> None:
        self.fps_label.setText(
            f"fps: {stats.get('fps', 0):.1f} | frames: {stats.get('frames', 0)}"
        )

    def _on_status(self, message: str, level: str) -> None:
        prefix = {"error": "ERROR: ", "warn": "WARN: "}.get(level, "")
        self.statusBar().showMessage(prefix + message, 8000)

    # ================================================================= выход
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 — Qt API
        if self.settings.autosave:
            try:
                self.settings = self._collect_settings()
                save_settings(self.settings, default_settings_path())
            except OSError:
                pass
        if self._worker is not None:
            self._worker.request_stop()
            self._worker.wait(5000)
            self._worker = None
        super().closeEvent(event)
