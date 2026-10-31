"""Race pace from lap-by-lap timing (Jolpica laps endpoint, no account needed).

For every race we compute each driver's typical lap time relative to the field:
  - lap 1 and safety-car / VSC / rain-affected laps are dropped (laps where the field
    median is more than 5% slower than the race's typical lap),
  - pit in/out laps and incidents are dropped (laps more than 4% slower than that lap's
    field median),
  - a driver's pace = median of his remaining laps relative to each lap's field median;
    `pace_gap` is the distance from the fastest driver, in %.

Jolpica allows ~500 requests/hour and returns 100 lap times per request (~13 requests
per race), so the full download is slow. Progress is cached and the script resumes.

Usage:  python backend/race_pace.py [first_season] [last_season]   (default 2018..now)
"""
import json
import sys
import time

import numpy as np
import pandas as pd
import requests

from config import CACHE, DATA, JOLPICA

MIN_INTERVAL = 7.4  # seconds between requests: ~486/hour, under the 500/hour limit
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "f1-predictor (personal project)"
_last = 0.0


def _get(path: str, params: dict) -> dict:
    global _last
    for attempt in range(8):
        wait = MIN_INTERVAL - (time.time() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.time()
        try:
            r = SESSION.get(f"{JOLPICA}/{path}", params=params, timeout=30)
        except requests.RequestException:
            time.sleep(30)
            continue
        if r.status_code == 429:
            time.sleep(120 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"giving up on {path}")


def lap_seconds(t: str) -> float:
    m, s = t.split(":") if ":" in t else ("0", t)
    return int(m) * 60 + float(s)


def fetch_laps(season: int, rnd: int) -> pd.DataFrame:
    cache = CACHE / f"laps_{season}_{rnd}.json"
    if cache.exists():
        return pd.DataFrame(json.loads(cache.read_text()))
    rows, offset, total = [], 0, None
    while total is None or offset < total:
        mr = _get(f"{season}/{rnd}/laps.json", {"limit": 100, "offset": offset})["MRData"]
        total = int(mr["total"])
        for race in mr["RaceTable"]["Races"]:
            for lap in race["Laps"]:
                for t in lap["Timings"]:
                    rows.append({"lap": int(lap["number"]), "driverId": t["driverId"],
                                 "t": lap_seconds(t["time"])})
        offset += 100
        if total == 0:
            break
    cache.write_text(json.dumps(rows))
    return pd.DataFrame(rows)


def pace_from_laps(laps: pd.DataFrame) -> pd.DataFrame:
    if laps.empty:
        return pd.DataFrame(columns=["driverId", "pace_gap", "clean_laps"])
    laps = laps[laps.lap > 1].copy()
    laps["field"] = laps.groupby("lap").t.transform("median")
    typical = laps.groupby("lap").field.first().median()
    laps = laps[laps.field <= typical * 1.05]          # no SC / VSC / heavy rain laps
    laps["rel"] = laps.t / laps.field
    laps = laps[laps.rel <= 1.04]                      # no pit laps / incidents
    p = laps.groupby("driverId").agg(rel=("rel", "median"), clean_laps=("rel", "size")).reset_index()
    p = p[p.clean_laps >= 8]
    p["pace_gap"] = ((p.rel - p.rel.min()) * 100).round(3)
    return p[["driverId", "pace_gap", "clean_laps"]]


def build_table() -> pd.DataFrame:
    """Rebuild data/race_pace.csv from every cached race."""
    out = []
    for f in CACHE.glob("laps_*.json"):
        _, s, r = f.stem.split("_")
        p = pace_from_laps(pd.DataFrame(json.loads(f.read_text())))
        out.append(p.assign(season=int(s), round=int(r)))
    df = pd.concat(out, ignore_index=True) if out else pd.DataFrame(
        columns=["driverId", "pace_gap", "clean_laps", "season", "round"])
    df.to_csv(DATA / "race_pace.csv", index=False)
    return df


def download(first: int, last: int):
    results = pd.read_csv(DATA / "results.csv")
    races = results[(results.season >= first) & (results.season <= last)][["season", "round"]] \
        .drop_duplicates().sort_values(["season", "round"], ascending=False)
    todo = [(s, r) for s, r in races.itertuples(index=False)
            if not (CACHE / f"laps_{s}_{r}.json").exists()]
    print(f"{len(todo)} races to download (~{len(todo) * 13 * MIN_INTERVAL / 3600:.1f} h)", flush=True)
    for i, (s, r) in enumerate(todo, 1):
        fetch_laps(s, r)
        print(f"[{i}/{len(todo)}] {s} round {r}", flush=True)
        if i % 10 == 0:
            build_table()
    build_table()
    print("done", flush=True)


if __name__ == "__main__":
    first = int(sys.argv[1]) if len(sys.argv) > 1 else 2018
    last = int(sys.argv[2]) if len(sys.argv) > 2 else 2100
    download(first, last)
