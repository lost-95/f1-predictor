"""Export the web app as a static site (no Python server needed).

Every API response the frontend uses is precomputed into a JSON file, and the frontend
switches to reading those files (window.F1_STATIC). The result can be served by any
static host, also under a sub-path such as lorenzosantarelli.com/f1-predictor/.

Usage:  python backend/export_static.py [output_dir]   (default: dist/)
"""
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import app as A
from config import FRONTEND, ROOT


def main(out: Path):
    if out.exists():
        shutil.rmtree(out)
    api = out / "api"
    api.mkdir(parents=True)
    shutil.copytree(FRONTEND, out / "static", ignore=shutil.ignore_patterns("index.html"))
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    marker = '<script src="static/i18n.js'
    assert marker in html, "index.html must load static/i18n.js with a relative path"
    html = html.replace(marker, '<script>window.F1_STATIC = true;</script>\n  ' + marker, 1)
    (out / "index.html").write_text(html, encoding="utf-8")

    def dump(name: str, obj):
        (api / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    for scenario in ("auto", "dry", "wet"):
        dump(f"next-{scenario}", A.api_next(scenario))
    dump("fanta", A.api_fanta())
    seasons = A.api_seasons()
    dump("seasons", seasons)
    for s in seasons:
        dump(f"season-{s['season']}", A.api_season(s["season"]))
    results = A.tables()["results"]
    races = results[["season", "round"]].drop_duplicates()
    for season, rnd in races.itertuples(index=False):
        dump(f"race-{season}-{rnd}", A.api_race(int(season), int(rnd)))
    dump("teams", A.api_teams())
    dump("model", A.api_model())
    status = A.api_status()
    status["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    dump("status", status)
    n = len(list(api.glob("*.json")))
    print(f"Static site written to {out} ({n} JSON files)")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "dist")
