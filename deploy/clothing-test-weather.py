"""Refresh synthetic evidence in the explicitly marked local clothing trial only."""
import json
import os
import time
from datetime import datetime, timezone

from backend import db, jobs

MARKER = "indigo-clothing-disposable-v1"
CONFIG = {
    "clothing_local_test": MARKER,
    "forecast_enabled": True,
    "latitude": 0.0,
    "longitude": 0.0,
    "timezone": "Etc/UTC",
    "placement": "outdoors",
    "environment_mode": "purpleair",
    "pm_method": "cf1",
    "sensor_source": "local",
}


def seed():
    if (os.environ.get("CLOTHING_SYNTHETIC_DISPOSABLE") != MARKER
            or os.environ.get("DISABLE_JOBS") != "1"):
        raise SystemExit("Refusing: requires the marked disposable trial with jobs disabled.")
    settings = db.settings()
    if settings:
        allowed = set(CONFIG) | {
            "clothing_provider", "clothing_state",
            "clothing_weather_source", "clothing_sensor_source",
        }
        if (set(settings) - allowed
                or any(settings.get(k) != v for k, v in CONFIG.items())):
            raise SystemExit("Refusing: settings are not the synthetic trial configuration.")
    with db.connect() as con:
        if not settings and any(con.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                                for table in ("readings", "raw_samples", "forecasts", "summaries")):
            raise SystemExit("Refusing: an unmarked database already contains weather data.")
        if con.execute("""SELECT 1 FROM readings r LEFT JOIN raw_samples s ON s.ts=r.ts
                          WHERE s.ts IS NULL LIMIT 1""").fetchone():
            raise SystemExit("Refusing: readings without synthetic provenance.")
        for row in con.execute("SELECT payload FROM raw_samples"):
            if json.loads(row["payload"]).get("SensorId") != MARKER:
                raise SystemExit("Refusing: non-synthetic sensor data.")
    now = int(time.time())
    midnight = int(datetime.fromtimestamp(now, timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp())
    db.set_settings(CONFIG)
    jobs.store_reading({
        "SensorId": MARKER,
        "DateTime": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "current_temp_f": 76,
        "current_humidity": 45,
        "pm2_5_cf_1": 6,
        "pm2_5_cf_1_b": 6,
    }, CONFIG, now)
    rows = []
    for hour in range(48):
        temp = 62 + (hour % 24 >= 12) * 12
        rows.append((now, midnight + hour * 3600, "weather", temp, 45, None,
                     3, 20, None, 2, 8, temp - 2, 35, None, None, None))
    jobs.store_forecasts(rows, CONFIG)
    print("Synthetic weather refreshed (UTC); no network or AI request made.")
    if datetime.fromtimestamp(now, timezone.utc).hour >= 23:
        print("UTC is past 23:00: today's clothing periods have ended. Try after 00:00 UTC.")


if __name__ == "__main__":
    seed()
