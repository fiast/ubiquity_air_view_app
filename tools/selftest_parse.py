"""Самопроверка парсеров протокола на реальных строках с устройства airOS 6.3.11."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from airview.protocol import (  # noqa: E402
    hexdump,
    parse_configuration,
    parse_frame,
    parse_result,
    parse_scan_range,
)

CONFIGURATION = (
    "CONFIGURATION: 2,XW.v6.3.11,Tue Apr 25 16:48:45 EEST 2023,0xe4e5,PowerBeam M5,"
    "PowerBeam M5 400,24A43CE88865,P5B,24A43CE88865,0,0xe4e5,XW.v6.3.11,"
    "Tue Apr 25 16:48:45 EEST 2023,"
    "5725000000|5825000000|5.725-5.825 (U-NII Upper Band)~"
    "5150000000|5250000000|5.150-5.250 (U-NII Lower Band)~"
    "5470000000|5725000000|5.470-5.725 (U-NII Worldwide),"
    "312500,"
    "184|4920000000|20000000~220|6100000000|20000000,,1,dbm,"
    "dBm - detected power per carrier,1,-150,4920,6100"
)

FRAME = "FRAME:  15,-114,-111,-119,-116,-115,-121,-118,-121,-114"


def main() -> int:
    cfg = parse_configuration(CONFIGURATION)
    assert cfg.version == "XW.v6.3.11", cfg.version
    assert cfg.model_full == "PowerBeam M5 400"
    assert cfg.bin_hz == 312500
    assert len(cfg.bands) == 3, cfg.bands
    assert cfg.bands[0].start_hz == 5725000000
    assert cfg.full_start_hz == 4920000000 and cfg.full_end_hz == 6100000000
    assert cfg.bins_for(5150000000, 5250000000) == 320
    assert cfg.bins_for(5150000000, 5850000000) == 2240
    assert len(cfg.channels) == 2

    assert parse_scan_range("SCAN RANGE: 5150000000,5250000000") == (5150000000, 5250000000)
    assert parse_result("RESULT: 0") == 0
    assert parse_frame(FRAME)[0] == 15
    assert parse_frame(FRAME)[1].size == 9
    assert parse_frame("garbage") is None

    print("device:", cfg.describe())
    print("bands:", [b.label for b in cfg.bands])
    print("bins 5150-5850:", cfg.bins_for(5150000000, 5850000000))
    print("hexdump:", hexdump(b"FRAME: 1,2\n", 16))
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
