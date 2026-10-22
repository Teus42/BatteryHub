import os
from pathlib import Path

APP_NAME = "Battery Hub"
DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "BatteryHub"

DEFAULT_SETTINGS = {
    "port": "8765",
    # Sem leitura por mais que isso => dispositivo considerado offline.
    # O app Android envia a cada 15 min, mas o WorkManager pode atrasar.
    "offline_minutes": "45",
    "low_battery_percent": "20",
}

READINGS_RETENTION_DAYS = 30
PAIRING_CODE_TTL_SECONDS = 10 * 60
