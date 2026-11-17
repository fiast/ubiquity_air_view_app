"""Проверки без GUI: JSON-настройки, replay-источник, attach к уже запущенному AirView."""

from __future__ import annotations

import sys
import time
import urllib.request
import urllib.parse
import http.cookiejar
import ssl
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from airview.config import Settings, load_settings, save_settings  # noqa: E402
from airview.datasource import MockDataSource, ReplayDataSource  # noqa: E402
from airview.airview_client import AirViewClient, AirViewError  # noqa: E402


def check_settings(tmp: Path) -> None:
    s = Settings(host="10.0.0.1", port=18888, username="u", password="secret",
                 remember_password=False, band_index=3, custom_start_mhz=5200)
    save_settings(s, tmp)
    text = tmp.read_text(encoding="utf-8")
    assert "secret" not in text, "пароль не должен сохраняться без remember_password"
    back = load_settings(tmp)
    assert back.host == "10.0.0.1" and back.band_index == 3 and back.custom_start_mhz == 5200
    s.remember_password = True
    save_settings(s, tmp)
    assert "secret" in tmp.read_text(encoding="utf-8")
    print("settings round-trip: OK")


def check_replay(path: Path) -> None:
    src = ReplayDataSource(path)
    src.start()
    frames = []
    for _ in range(3):
        f = src.read_frame()
        if f is not None:
            frames.append(f)
    assert frames and frames[0].size == 3776, "replay должен дать кадры по 3776 бинов"
    src.stop()
    print(f"replay: OK ({len(frames)} кадров, {frames[0].size} бинов)")


def check_mock() -> None:
    src = MockDataSource(bins=512, frame_rate_hz=50)
    src.start()
    t0 = time.time()
    n = 0
    while time.time() - t0 < 0.6:
        if src.read_frame() is not None:
            n += 1
    src.stop()
    assert n >= 3, n
    print(f"mock: OK ({n} кадров)")


def check_attach(host: str, user: str, password: str) -> None:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj), urllib.request.HTTPSHandler(context=ctx)
    )
    base = f"https://{host}"
    op.open(base + "/login.cgi", timeout=10)
    op.open(base + "/login.cgi",
            data=urllib.parse.urlencode({"username": user, "password": password}).encode(),
            timeout=10)
    resp = op.open(base + "/airview.cgi?start=1", timeout=20)
    assert "jnlp" in (resp.geturl() or "") or "<jnlp" in resp.read().decode("utf-8", "replace")

    settings = Settings(host=host, username=user, password=password, dump_enabled=False)
    client = AirViewClient(settings, status_cb=lambda t, l="info": print(f"  [{l}] {t}"))
    t0 = time.time()
    while time.time() - t0 < 60:
        if client.probe_running():
            break
        time.sleep(0.5)
    assert client.probe_running(), "порт 18888 не открылся"
    client.start(attach=True)
    got = 0
    for _ in range(20):
        f = client.read_frame()
        if f is not None:
            got += 1
        if got >= 3:
            break
        time.sleep(0.03)
    client.stop()
    assert got >= 1, "attach не дал кадров"
    print(f"attach: OK ({got} кадров, {client.bins} бинов)")


def main() -> int:
    host = "192.168.1.20"
    user = "manager"
    password = sys.argv[1] if len(sys.argv) > 1 else ""

    tmp = Path("settings_test.json")
    check_settings(tmp)
    tmp.unlink(missing_ok=True)
    check_mock()

    dumps = sorted(Path("airview_dumps").glob("airview-*.dat"))
    if dumps:
        check_replay(dumps[-1])
    else:
        print("replay: пропущен (нет дампа)")

    if password:
        check_attach(host, user, password)
    else:
        print("attach: пропущен (нет пароля)")
    print("ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
