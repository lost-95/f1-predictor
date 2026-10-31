"""Race-day weather for every Grand Prix from Open-Meteo.

Source: Open-Meteo Historical Weather API (ERA5 / ERA5-Land reanalysis from ECMWF,
hourly, 1940-today) for past races, and the Open-Meteo Forecast API for recent and
upcoming races. Values are taken at the circuit coordinates during the race window
(start time -> +2h, local time). When the start time is unknown (old seasons), 14:00
local is assumed.

Usage:  python backend/weather.py
"""
import json
import time
from datetime import date, datetime, timedelta

import pandas as pd
import requests

from config import CACHE, DATA, METEO_ARCHIVE, METEO_FORECAST

HOURLY = "temperature_2m,relative_humidity_2m,precipitation,cloud_cover,wind_speed_10m,weather_code"
SESSION = requests.Session()
_last = 0.0


def _get(url: str, params: dict) -> dict:
    global _last
    for attempt in range(5):
        wait = 0.2 - (time.time() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.time()
        r = SESSION.get(url, params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(10 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("Open-Meteo rate limit")


def race_start_local(race: dict, utc_offset_sec: int) -> datetime:
    t = str(race.get("time") or "")
    if t and t != "nan":
        start_utc = datetime.fromisoformat(f'{race["date"]}T{t.replace("Z", "")}')
        return start_utc + timedelta(seconds=utc_offset_sec)
    return datetime.fromisoformat(f'{race["date"]}T14:00:00')


def fetch_hourly(race: dict) -> tuple[dict, str]:
    """Return (open-meteo response, source label)."""
    d = date.fromisoformat(race["date"])
    params = {
        "latitude": race["lat"], "longitude": race["lng"],
        "start_date": race["date"], "end_date": race["date"],
        "hourly": HOURLY, "timezone": "auto", "wind_speed_unit": "kmh",
    }
    today = date.today()
    if d <= today - timedelta(days=6):
        data = _get(METEO_ARCHIVE, params)
        if any(v is not None for v in data["hourly"]["temperature_2m"]):
            return data, "ERA5 reanalysis (Open-Meteo archive)"
    if d <= today + timedelta(days=15):
        return _get(METEO_FORECAST, params), (
            "Open-Meteo forecast" if d >= today else "Open-Meteo recent analysis")
    return None, "not available yet"


def summarize(race: dict, data: dict | None, source: str) -> dict:
    out = {"season": race["season"], "round": race["round"], "weather_source": source}
    if data is None:
        return out
    h = pd.DataFrame(data["hourly"])
    h["time"] = pd.to_datetime(h["time"])
    start = race_start_local(race, data.get("utc_offset_seconds", 0)).replace(minute=0, second=0)
    win = h[(h.time >= start) & (h.time < start + timedelta(hours=2))]
    pre = h[(h.time >= start - timedelta(hours=3)) & (h.time < start)]
    if win.empty:
        return out
    rain = float(win.precipitation.sum())
    pre_rain = float(pre.precipitation.sum())
    out.update({
        "race_start_local": start.isoformat(timespec="minutes"),
        "temp_c": round(float(win.temperature_2m.mean()), 1),
        "humidity": round(float(win.relative_humidity_2m.mean()), 0),
        "wind_kmh": round(float(win.wind_speed_10m.mean()), 1),
        "cloud_cover": round(float(win.cloud_cover.mean()), 0),
        "rain_mm": round(rain, 2),
        "pre_rain_mm": round(pre_rain, 2),
        "weather_code": int(win.weather_code.max()),
        # wet track: rain during the race, or significant rain just before the start
        "wet": int(rain >= 0.4 or pre_rain >= 1.0),
    })
    return out


def weather_for_race(race: dict, use_cache: bool = True) -> dict:
    cache_file = CACHE / f'weather_{race["season"]}_{race["round"]}.json'
    is_final = date.fromisoformat(race["date"]) <= date.today() - timedelta(days=6)
    if use_cache and cache_file.exists():
        cached = json.loads(cache_file.read_text())
        fresh = time.time() - cache_file.stat().st_mtime < 3 * 3600
        if (is_final and "ERA5" in cached.get("weather_source", "")) or fresh:
            return cached
    data, source = fetch_hourly(race)
    out = summarize(race, data, source)
    if data is not None:
        cache_file.write_text(json.dumps(out))
    return out


def build():
    races = pd.read_csv(DATA / "races.csv")
    limit = date.today() + timedelta(days=15)
    # final ERA5 values never change: reuse them from the existing table (no disk cache in CI)
    final = {}
    if (DATA / "weather.csv").exists():
        old = pd.read_csv(DATA / "weather.csv")
        for row in old[old.weather_source.astype(str).str.contains("ERA5")].to_dict("records"):
            final[(row["season"], row["round"])] = {k: v for k, v in row.items() if pd.notna(v)}
    rows = []
    for i, race in enumerate(races.to_dict("records")):
        if date.fromisoformat(race["date"]) > limit:
            rows.append({"season": race["season"], "round": race["round"],
                         "weather_source": "not available yet"})
            continue
        key = (race["season"], race["round"])
        rows.append(final[key] if key in final else weather_for_race(race))
        if i % 50 == 0:
            print(f"{i}/{len(races)} {race['season']} {race['raceName']}", flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(DATA / "weather.csv", index=False)
    print(f"Saved weather for {df['temp_c'].notna().sum()} races "
          f"({int(df['wet'].sum())} wet).")
    return df


if __name__ == "__main__":
    build()
