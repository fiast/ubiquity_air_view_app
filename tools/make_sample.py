"""Обрезает большой .dat-дамп до 40 кадров — для samples/ в репозитории."""

from pathlib import Path

src = Path("airview_dumps/airview-20261117-145341.dat")
dst = Path("samples/powerbeam-m5-5150-5850.dat")
dst.parent.mkdir(parents=True, exist_ok=True)

out = []
frames = 0
for line in src.read_text(encoding="utf-8", errors="replace").splitlines():
    payload = line.split("\x01", 1)[-1]
    if payload.startswith("FRAME:"):
        frames += 1
        if frames > 40:
            break
    out.append(line)

dst.write_text("\n".join(out) + "\n", encoding="utf-8")
print("sample:", dst, dst.stat().st_size, "bytes, frames:", frames)
