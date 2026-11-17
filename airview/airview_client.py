"""Клиент airView: веб-логин airOS → запуск AirView → TCP 18888 → кадры спектра.

Схема (подтверждена на PowerBeam M5 400, XW.v6.3.11):
    1. GET  https://host/login.cgi                     — получить cookie сессии
    2. POST https://host/login.cgi username/password   — аутентификация
    3. GET  https://host/airview.cgi?start=1           — включить режим AirView (redirect на .jnlp)
    4. ожидание открытия TCP-порта 18888 (у airOS 6.3 — ~11-13 с)
    5. CONNECT: → CONFIGURATION: ...
    6. REQUEST RANGE: <start_hz>,<end_hz> → SCAN RANGE: ...
    7. START SCAN: → RESULT: 0
    8. GET FRAME: → FRAME: <номер>,<v1>,...,<vN>   (повторять, кадры дедуплицировать по номеру)

Все параметры (таймауты, диапазон, логин/пароль) берутся из Settings.
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import requests
import urllib3

from .config import Settings
from .datasource import DataSource, DataSourceError
from .protocol import (
    CMD_CONNECT,
    CMD_GET_FRAME,
    CMD_START_SCAN,
    DeviceConfig,
    cmd_request_range,
    hexdump,
    parse_configuration,
    parse_frame,
    parse_result,
    parse_scan_range,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

StatusCallback = Callable[[str, str], None]      # (текст, уровень: info|warn|error)
DiagCallback = Callable[[str, bytes], None]      # (направление: ">>"|"<<", сырые байты)


class AirViewError(DataSourceError):
    """Общая ошибка клиента airView."""


class AuthError(AirViewError):
    """Не удалось аутентифицироваться на веб-интерфейсе устройства."""


class AirViewClient(DataSource):
    """Источник данных: живое устройство airOS через порт 18888."""

    name = "device"

    def __init__(
        self,
        settings: Settings,
        status_cb: Optional[StatusCallback] = None,
        diag_cb: Optional[DiagCallback] = None,
    ) -> None:
        super().__init__()
        self.settings = settings
        self._status_cb = status_cb
        self._diag_cb = diag_cb

        self._session: Optional[requests.Session] = None
        self._sock: Optional[socket.socket] = None
        self._rx = bytearray()
        self._lock = threading.RLock()
        self._running = False
        self._launched = False

        self._dump_fh = None
        self._dump_path: Optional[Path] = None
        self._diag_path: Optional[Path] = None

        self.start_hz = 0
        self.end_hz = 0

    # ------------------------------------------------------------------ утилиты
    def _status(self, text: str, level: str = "info") -> None:
        if self._status_cb is not None:
            try:
                self._status_cb(text, level)
            except Exception:  # noqa: BLE001 — колбэк не должен ронять клиент
                pass

    def _diag(self, direction: str, payload: bytes) -> None:
        if not self.settings.diagnostics:
            return
        line = f"{time.strftime('%H:%M:%S')} {direction} {hexdump(payload, self.settings.diag_bytes)}"
        if self._diag_cb is not None:
            try:
                self._diag_cb(direction, payload)
            except Exception:  # noqa: BLE001
                pass
        if self._diag_path is not None:
            try:
                with self._diag_path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass

    def _dump_line(self, text: str) -> None:
        """Пишет строку в .dat (формат как у airviewer.py: таймстамп \\x01 строка)."""
        if self._dump_fh is not None:
            try:
                self._dump_fh.write(f"{time.time():.6f}\x01{text}\n")
                self._dump_fh.flush()
            except (OSError, ValueError):
                pass

    # ------------------------------------------------------------------ порт
    def probe_running(self, timeout: float = 1.5) -> bool:
        """Открыт ли уже порт airView (значит, режим уже запущен)."""
        try:
            with socket.create_connection(
                (self.settings.host, self.settings.port), timeout=timeout
            ):
                return True
        except OSError:
            return False

    # ------------------------------------------------------------------ HTTP
    def _http(self) -> requests.Session:
        if self._session is None:
            s = requests.Session()
            s.verify = False
            s.headers.update({"User-Agent": "airView Analyzer/1.0"})
            self._session = s
        return self._session

    def _url(self, path: str) -> str:
        return f"https://{self.settings.host}:443{path}"

    def login(self) -> None:
        """Аутентификация на веб-интерфейсе airOS (5.x/6.x: /login.cgi).

        После выхода из AirView точка перезагружает радио и веб-интерфейс на несколько
        секунд пропадает, поэтому подключение повторяем.
        """
        attempts = max(1, int(self.settings.web_retries))
        last: Optional[AirViewError] = None
        for attempt in range(1, attempts + 1):
            try:
                self._login_once()
                return
            except AuthError:
                raise
            except AirViewError as exc:
                last = exc
                if attempt < attempts:
                    self._status(
                        f"веб-интерфейс недоступен (попытка {attempt}/{attempts}), "
                        f"жду {self.settings.web_retry_delay_s:.0f} с…",
                        "warn",
                    )
                    time.sleep(self.settings.web_retry_delay_s)
        assert last is not None
        raise last

    def _login_once(self) -> None:
        s = self._http()
        timeout = self.settings.http_timeout_s
        self._status("WEB: открываю страницу логина", "info")
        try:
            s.get(self._url("/login.cgi"), timeout=timeout)
            resp = s.post(
                self._url("/login.cgi"),
                data={"username": self.settings.username, "password": self.settings.password},
                timeout=timeout,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            raise AirViewError(f"веб-интерфейс недоступен: {exc}") from exc

        if "Invalid credentials" in resp.text:
            raise AuthError("неверный логин или пароль")

        # Проверяем, что сессия действительно авторизована
        try:
            check = s.get(self._url("/index.cgi"), timeout=timeout)
        except requests.RequestException as exc:
            raise AirViewError(f"ошибка проверки сессии: {exc}") from exc
        if "<title>Login</title>" in check.text:
            raise AuthError("логин не принят (проверьте пользователя/пароль)")
        self._status("WEB: вход выполнен", "info")

    def _launch_once(self) -> bool:
        s = self._http()
        resp = s.get(
            self._url("/airview.cgi?start=1"),
            timeout=self.settings.http_timeout_s,
            allow_redirects=True,
        )
        body = resp.text or ""
        ok = resp.url.lower().endswith(".jnlp") or "<jnlp" in body.lower()
        if not ok and "login.cgi" in resp.url:
            return False
        if resp.status_code in (401, 403):
            raise AuthError(
                "устройство отказало в запуске AirView (недостаточно прав?). "
                "Запустите AirView вручную: Tools → AirView → Launch, затем подключитесь."
            )
        return ok

    def launch_airview(self) -> None:
        """Включает режим AirView через веб-интерфейс (GET airview.cgi?start=1)."""
        self._status("WEB: запускаю режим AirView", "info")
        if self._launch_once():
            self._launched = True
            self._status("WEB: AirView включён (JNLP получен)", "info")
            return
        # Сессия могла истечь — повторяем логин и запуск один раз
        self._status("WEB: сессия истекла, повторный вход", "warn")
        self.login()
        if not self._launch_once():
            raise AirViewError("не удалось включить AirView (устройство не отдало JNLP)")
        self._launched = True
        self._status("WEB: AirView включён (JNLP получен)", "info")

    def stop_airview(self, best_effort: bool = True) -> None:
        """Выключает режим AirView (GET airview.cgi?start=0). Ошибки игнорируются."""
        try:
            self._http().get(
                self._url("/airview.cgi?start=0"),
                timeout=min(5.0, self.settings.http_timeout_s),
                allow_redirects=True,
            )
            self._status("WEB: AirView выключен", "info")
        except requests.RequestException:
            if not best_effort:
                raise
        self._launched = False

    # ------------------------------------------------------------------ сокет
    def _wait_port(self) -> None:
        deadline = time.monotonic() + self.settings.launch_timeout_s
        self._status("Ожидаю открытия порта 18888…", "info")
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(
                    (self.settings.host, self.settings.port), timeout=1.5
                ):
                    self._status("Порт 18888 открыт", "info")
                    return
            except OSError:
                time.sleep(0.5)
        raise AirViewError(
            f"порт {self.settings.port} не открылся за {self.settings.launch_timeout_s:.0f} с"
        )

    def _open_socket(self) -> None:
        try:
            sock = socket.create_connection(
                (self.settings.host, self.settings.port),
                timeout=self.settings.connect_timeout_s,
            )
        except OSError as exc:
            raise AirViewError(f"не удалось подключиться к {self.settings.host}:{self.settings.port}: {exc}") from exc
        sock.settimeout(self.settings.io_timeout_s)
        self._sock = sock
        self._rx.clear()

    def _send(self, cmd: bytes) -> None:
        if self._sock is None:
            raise AirViewError("сокет не открыт")
        with self._lock:
            try:
                self._sock.sendall(cmd)
            except OSError as exc:
                raise AirViewError(f"ошибка отправки: {exc}") from exc
        self._diag(">>", cmd)
        self._dump_line(cmd.decode("ascii", "replace").rstrip("\n"))

    def _read_line(self) -> bytes:
        """Читает одну строку (без \\n). Пустые байты — соединение закрыто."""
        with self._lock:
            while b"\n" not in self._rx:
                if self._sock is None:
                    return b""
                try:
                    chunk = self._sock.recv(65536)
                except socket.timeout as exc:
                    raise AirViewError("таймаут чтения от устройства") from exc
                except OSError as exc:
                    raise AirViewError(f"ошибка чтения: {exc}") from exc
                if not chunk:
                    return b""
                self._rx.extend(chunk)
            line, _, rest = self._rx.partition(b"\n")
            self._rx = bytearray(rest)
        self._diag("<<", line + b"\n")
        self._dump_line(line.decode("ascii", "replace"))
        # срезаем IAC-последовательности телнета, если устройство вдруг начнёт согласование
        if b"\xff" in line:
            cleaned = bytearray()
            i = 0
            while i < len(line):
                if line[i] == 0xFF and i + 1 < len(line):
                    code = line[i + 1]
                    if code == 0xFF:
                        cleaned.append(0xFF)
                        i += 2
                    elif code in (0xFB, 0xFC, 0xFD, 0xFE):
                        i += 3
                    elif code in (0xFA,):
                        i += 2
                    else:
                        i += 2
                else:
                    cleaned.append(line[i])
                    i += 1
            line = bytes(cleaned)
        return line

    # ------------------------------------------------------------------ старт
    def start(self, attach: bool = False) -> None:
        """Подняться до состояния «можно читать кадры».

        attach=True — подключиться к уже запущенному AirView (без логина/запуска).
        """
        if attach:
            self._status("Подключаюсь к уже запущенному AirView", "info")
            self._open_socket()
        else:
            self.login()
            self.launch_airview()
            self._wait_port()
            self._open_socket()

        self._open_dumps()
        self._handshake()
        self._running = True

    def _open_dumps(self) -> None:
        if not self.settings.dump_enabled:
            return
        try:
            if self.settings.record_file:
                self._dump_path = Path(self.settings.record_file)
                self._dump_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                self._dump_path = self.settings.dump_path_for()
            self._dump_fh = self._dump_path.open("a", encoding="utf-8", errors="replace")
            self._status(f"Дамп: {self._dump_path.name}", "info")
            if self.settings.diagnostics:
                self._diag_path = self._dump_path.with_suffix(".log")
        except OSError as exc:
            self._status(f"не удалось открыть дамп: {exc}", "warn")

    def _handshake(self) -> None:
        """CONNECT → CONFIGURATION, затем REQUEST RANGE + START SCAN."""
        self._send(CMD_CONNECT)
        line = self._read_line()
        if not line.lower().startswith(b"configuration:"):
            raise AirViewError(f"неожиданный ответ на CONNECT: {line[:120]!r}")
        cfg = parse_configuration(line)
        self.device = cfg
        self._status(f"Устройство: {cfg.describe()}", "info")

        start_hz, end_hz = self.settings.resolve_range(cfg)
        if end_hz <= start_hz:
            raise AirViewError("не удалось определить диапазон сканирования")
        self._apply_range(start_hz, end_hz)

    def _apply_range(self, start_hz: int, end_hz: int) -> None:
        self._send(cmd_request_range(start_hz, end_hz))
        line = self._read_line()
        actual = parse_scan_range(line)
        if actual is None:
            raise AirViewError(f"неожиданный ответ на REQUEST RANGE: {line[:120]!r}")
        self.start_hz, self.end_hz = actual
        self.last_frame_nr = -1
        self._status(
            f"Диапазон: {self.start_hz / 1e6:.1f}–{self.end_hz / 1e6:.1f} МГц, "
            f"бинов: {self.bins}",
            "info",
        )

        self._send(CMD_START_SCAN)
        line = self._read_line()
        result = parse_result(line)
        if result is None:
            raise AirViewError(f"неожиданный ответ на START SCAN: {line[:120]!r}")
        if result != 0:
            raise AirViewError(f"устройство отказало в старте сканирования (RESULT={result})")

    def set_range(self, start_hz: int, end_hz: int) -> None:
        """Сменить диапазон на лету (REQUEST RANGE + START SCAN)."""
        if self._sock is None:
            raise AirViewError("нет соединения с устройством")
        self._apply_range(int(start_hz), int(end_hz))

    # ------------------------------------------------------------------ чтение
    def read_frame(self) -> Optional[np.ndarray]:
        """Отправляет GET FRAME и возвращает новый кадр; None — кадр повторился."""
        self._send(CMD_GET_FRAME)
        line = self._read_line()
        if not line:
            raise AirViewError("соединение закрыто устройством")

        parsed = parse_frame(line)
        if parsed is None:
            rng = parse_scan_range(line)
            if rng is not None:  # устройство само сменило диапазон — учитываем
                self.start_hz, self.end_hz = rng
                self.last_frame_nr = -1
            return None

        frame_nr, values = parsed
        if frame_nr <= self.last_frame_nr:
            return None
        self.last_frame_nr = frame_nr
        self.frames_received += 1
        return values

    # ------------------------------------------------------------------ стоп
    def stop(self) -> None:
        self._running = False
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
        if self._dump_fh is not None:
            try:
                self._dump_fh.close()
            except OSError:
                pass
            self._dump_fh = None
        if self._session is not None:
            try:
                self._session.close()
            except Exception:  # noqa: BLE001
                pass
            self._session = None

    def shutdown(self) -> None:
        """Полное завершение: закрыть сокет и, по настройке, выключить AirView."""
        self.stop()
        if self.settings.stop_airview_on_exit and self._launched:
            try:
                self.stop_airview()
            except Exception:  # noqa: BLE001 — при выходе не важна точность
                pass
