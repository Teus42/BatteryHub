"""Gera assets/battery_hub.ico (16 a 256 px) a partir do ícone desenhado no tema."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "pc"))
from battery_hub.theme import make_icon_image  # noqa: E402

sizes = (16, 24, 32, 48, 64, 128, 256)
out = root / "assets" / "battery_hub.ico"
make_icon_image(256).save(out, format="ICO", sizes=[(s, s) for s in sizes])
print(out)
