"""Features of a race must not change when that race's result is hidden."""
import numpy as np
import pandas as pd

from config import DATA
from features import FEATURES_ELO, FEATURES_GRID, FEATURES_PACE, make_dataset

FEATURES = FEATURES_GRID + FEATURES_ELO + FEATURES_PACE

results = pd.read_csv(DATA / "results.csv")
races = pd.read_csv(DATA / "races.csv")
weather = pd.read_csv(DATA / "weather.csv")
quali = pd.read_csv(DATA / "qualifying.csv")
pace_file = DATA / "race_pace.csv"
pace = pd.read_csv(pace_file) if pace_file.exists() else None

ok = True
for season, rnd in [(2012, 5), (2021, 22), (2025, 10), (2026, 18)]:
    full = make_dataset(results, races, weather, quali, pace=pace)
    key = season * 100 + rnd
    hidden = results[(results.season * 100 + results["round"]) < key]
    hidden_pace = None if pace is None else pace[(pace.season * 100 + pace["round"]) < key]
    upcoming = results[(results.season == season) & (results["round"] == rnd)][
        ["season", "round", "driverId", "driverName", "driverCode", "constructorId",
         "constructorName", "grid"]]
    blind = make_dataset(hidden, races, weather, quali, upcoming, pace=hidden_pace)
    a = full[full.race_idx == key].set_index("driverId")[FEATURES].sort_index()
    b = blind[blind.race_idx == key].set_index("driverId")[FEATURES].sort_index()
    diff = ~np.isclose(a.values.astype(float), b.values.astype(float), equal_nan=True)
    bad = sorted({FEATURES[j] for j in np.where(diff)[1]})
    print(season, rnd, "OK" if not bad else f"LEAK in {bad}")
    ok &= not bad
print("PASS" if ok else "FAIL")
