"""Incremental update used by the scheduled GitHub Action.

Starts from the CSVs committed in the repo (no disk cache needed):
  1. re-downloads only the latest season (results, qualifying, standings, schedule),
  2. downloads lap times only for races not processed yet,
  3. fetches weather only for races without final (ERA5) values,
  4. retrains the models only when there are new race results (or FORCE_RETRAIN=true).

Usage:  python backend/update.py
"""
import hashlib
import os

import data_fetch
import race_pace
import train
import weather
from config import DATA, MODELS


def digest(path) -> str | None:
    return hashlib.md5(path.read_bytes()).hexdigest() if path.exists() else None


def main():
    before = digest(DATA / "results.csv")
    data_fetch.update_recent()
    race_pace.download(2018, 2100)
    weather.build()
    new_results = digest(DATA / "results.csv") != before
    force = os.environ.get("FORCE_RETRAIN", "").lower() == "true"
    if new_results or force or not (MODELS / "pre_quali.json").exists():
        print("Retraining models" + (" (forced)" if force else " (new results)"), flush=True)
        train.main()
    else:
        print("No new results: models unchanged.")


if __name__ == "__main__":
    main()
