"""Download F1 schedules and race results 2001..current from the Jolpica (Ergast) API.

Responses are cached on disk. Past seasons are fetched once; the current season
is refreshed when the cache is older than a few hours.

Usage:  python backend/data_fetch.py [--refresh]
"""
import json
import sys
import time
from datetime import date, datetime

import pandas as pd
import requests

from config import CACHE, DATA, FIRST_SEASON, JOLPICA

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "f1-predictor (personal project)"
CURRENT_SEASON = date.today().year
CURRENT_TTL_SEC = 3 * 3600
_last_call = 0.0


def get_json(path: str, params: dict | None = None, cache_name: str | None = None,
             ttl: float | None = None) -> dict:
    """GET with disk cache, polite pacing (Jolpica allows ~4 req/s, 500 req/h) and retry on 429."""
    global _last_call
    cache_file = CACHE / f"{cache_name}.json" if cache_name else None
    if cache_file and cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if ttl is None or age < ttl:
            return json.loads(cache_file.read_text(encoding="utf-8"))

    for attempt in range(6):
        wait = 0.35 - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()
        r = SESSION.get(f"{JOLPICA}/{path}", params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
        data = r.json()
        if cache_file:
            cache_file.write_text(json.dumps(data), encoding="utf-8")
        return data
    raise RuntimeError(f"Too many retries for {path}")


def fetch_paginated(path: str, key: str, cache_prefix: str, ttl: float | None) -> list[dict]:
    """Ergast paginates by result row (max 100); a race can be split across pages, so merge by round."""
    races: dict[int, dict] = {}
    offset, total = 0, None
    while total is None or offset < total:
        data = get_json(path, {"limit": 100, "offset": offset},
                        cache_name=f"{cache_prefix}_{offset}", ttl=ttl)
        mr = data["MRData"]
        total = int(mr["total"])
        for race in mr["RaceTable"]["Races"]:
            rnd = int(race["round"])
            if rnd in races:
                races[rnd][key].extend(race.get(key, []))
            else:
                races[rnd] = race
        offset += 100
        if total == 0:
            break
    return [races[k] for k in sorted(races)]


def fetch_schedule(season: int, ttl) -> list[dict]:
    data = get_json(f"{season}/races.json", {"limit": 100}, cache_name=f"schedule_{season}", ttl=ttl)
    return data["MRData"]["RaceTable"]["Races"]


def fetch_qualifying(season: int, rnd: int) -> list[dict]:
    """Qualifying for one race (used for the upcoming race's grid). Short cache."""
    data = get_json(f"{season}/{rnd}/qualifying.json", {"limit": 100},
                    cache_name=f"quali_{season}_{rnd}", ttl=1800)
    races = data["MRData"]["RaceTable"]["Races"]
    return races[0]["QualifyingResults"] if races else []


def lap_seconds(t: str | None) -> float | None:
    """'1:23.456' -> 83.456"""
    if not t or ":" not in t:
        return None
    try:
        m, s = t.split(":")
        return int(m) * 60 + float(s)
    except ValueError:
        return None


def quali_row(season: int, rnd: int, q: dict) -> dict:
    times = [lap_seconds(q.get(k)) for k in ("Q1", "Q2", "Q3")]
    times = [t for t in times if t]
    return {
        "season": season, "round": rnd,
        "driverId": q["Driver"]["driverId"],
        "driverName": f'{q["Driver"]["givenName"]} {q["Driver"]["familyName"]}',
        "driverCode": q["Driver"].get("code", q["Driver"]["familyName"][:3].upper()),
        "constructorId": q["Constructor"]["constructorId"],
        "constructorName": q["Constructor"]["name"],
        "q_pos": int(q["position"]),
        "q_best": min(times) if times else None,
    }


def add_quali_gap(q: pd.DataFrame) -> pd.DataFrame:
    """Gap to the fastest qualifying lap of the weekend, in % (capped at 7%)."""
    if q.empty:
        return q
    pole = q.groupby(["season", "round"]).q_best.transform("min")
    q["q_gap"] = ((q.q_best - pole) / pole * 100).clip(upper=7).round(3)
    return q


TABLES = ("races", "results", "qualifying", "standings")


def season_rows(season: int, ttl) -> tuple[list, list, list, list]:
    """(races, results, qualifying, standings) rows for one season."""
    race_rows, result_rows, quali_rows, standing_rows = [], [], [], []
    print(f"Season {season}...", flush=True)
    for race in fetch_schedule(season, ttl):
        loc = race["Circuit"]["Location"]
        race_rows.append({
            "season": season,
            "round": int(race["round"]),
            "raceName": race["raceName"],
            "date": race["date"],
            "time": race.get("time", ""),
            "circuitId": race["Circuit"]["circuitId"],
            "circuitName": race["Circuit"]["circuitName"],
            "locality": loc.get("locality", ""),
            "country": loc.get("country", ""),
            "lat": float(loc["lat"]),
            "lng": float(loc["long"]),
        })
    for race in fetch_paginated(f"{season}/results.json", "Results", f"results_{season}", ttl):
        rnd = int(race["round"])
        for res in race["Results"]:
            d, c = res["Driver"], res["Constructor"]
            result_rows.append({
                "season": season,
                "round": rnd,
                "driverId": d["driverId"],
                "driverName": f'{d["givenName"]} {d["familyName"]}',
                "driverCode": d.get("code", d["familyName"][:3].upper()),
                "nationality": d.get("nationality", ""),
                "constructorId": c["constructorId"],
                "constructorName": c["name"],
                "grid": int(res["grid"]),
                "position": int(res["position"]),
                "positionText": res["positionText"],
                "points": float(res["points"]),
                "laps": int(res["laps"]),
                "status": res["status"],
                "fastestLapRank": int(res.get("FastestLap", {}).get("rank", 0) or 0),
            })
    for race in fetch_paginated(f"{season}/qualifying.json", "QualifyingResults",
                                f"qualifying_{season}", ttl):
        for q in race["QualifyingResults"]:
            quali_rows.append(quali_row(season, int(race["round"]), q))
    # official standings (include sprint points, which race results alone do not)
    for kind in ("driver", "constructor"):
        data = get_json(f"{season}/{kind}standings.json", {"limit": 100},
                        cache_name=f"{kind}standings_{season}", ttl=ttl)
        lists = data["MRData"]["StandingsTable"]["StandingsLists"]
        for st in (lists[0][f"{kind.capitalize()}Standings"] if lists else []):
            row = {"season": season, "kind": kind, "position": int(st.get("position", 0) or 0),
                   "points": float(st["points"]), "wins": int(st["wins"])}
            if kind == "driver":
                d = st["Driver"]
                row.update(id=d["driverId"], name=f'{d["givenName"]} {d["familyName"]}',
                           team=st["Constructors"][-1]["name"] if st["Constructors"] else "")
            else:
                row.update(id=st["Constructor"]["constructorId"], name=st["Constructor"]["name"], team="")
            standing_rows.append(row)
    return race_rows, result_rows, quali_rows, standing_rows


def _save(frames: dict[str, pd.DataFrame]):
    for name, df in frames.items():
        df.to_csv(DATA / f"{name}.csv", index=False)
    (DATA / "last_update.json").write_text(json.dumps({"f1": datetime.now().astimezone().isoformat(timespec="seconds")}))
    print(f"Saved {len(frames['races'])} races, {len(frames['results'])} result rows.")


def build(refresh: bool = False):
    """Full download 2001..current season (past seasons come from the disk cache when present)."""
    parts = []
    for season in range(FIRST_SEASON, CURRENT_SEASON + 1):
        ttl = (0 if refresh else CURRENT_TTL_SEC) if season >= CURRENT_SEASON else None
        parts.append(season_rows(season, ttl))
    frames = {name: pd.DataFrame([r for p in parts for r in p[i]]) for i, name in enumerate(TABLES)}
    frames["qualifying"] = add_quali_gap(frames["qualifying"])
    _save(frames)
    return frames["races"], frames["results"]


def update_recent():
    """Incremental update: re-download only the latest season(s) and keep older rows from the CSVs.
    Used by the scheduled GitHub Action, which starts without the disk cache."""
    if not (DATA / "races.csv").exists():
        return build()
    old = {name: pd.read_csv(DATA / f"{name}.csv") for name in TABLES}
    seasons = sorted({CURRENT_SEASON, int(old["races"].season.max())})
    parts = [season_rows(s, ttl=0) for s in seasons]
    frames = {}
    for i, name in enumerate(TABLES):
        new = pd.DataFrame([r for p in parts for r in p[i]])
        if name == "qualifying":
            new = add_quali_gap(new)
        df = pd.concat([old[name][~old[name].season.isin(seasons)], new], ignore_index=True)
        frames[name] = df.sort_values(["season", "round"] if "round" in df else ["season"], kind="stable")
    _save(frames)
    return frames["races"], frames["results"]


if __name__ == "__main__":
    if "--recent" in sys.argv:
        update_recent()
    else:
        build(refresh="--refresh" in sys.argv)
