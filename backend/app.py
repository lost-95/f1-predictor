"""F1 Predictor web app (FastAPI). Run:  python backend/app.py  ->  http://127.0.0.1:8000"""
import json
import math
import threading
import time
import traceback
from functools import lru_cache

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import data_fetch
import fanta
import predict
import race_pace
import train
import weather as weather_mod
from config import DATA, FRONTEND
from features import FAMILY_LABEL, TEAM_FAMILY, team_season_table

app = FastAPI(title="F1 Predictor")
_pred_cache: dict[str, tuple[float, dict]] = {}
_refresh = {"running": False, "step": None, "error": None, "finished_at": None}


def clean(obj):
    """Make pandas/numpy values JSON-safe (NaN -> null)."""
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if hasattr(obj, "item"):
        obj = obj.item()
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def records(df: pd.DataFrame) -> list[dict]:
    return clean(df.to_dict("records"))


@lru_cache(maxsize=1)
def tables():
    t = {name: pd.read_csv(DATA / f"{name}.csv") for name in
         ("races", "results", "weather", "standings", "qualifying", "backtest")}
    t["report"] = json.loads((DATA / "model_report.json").read_text())
    return t


@app.get("/api/next")
def api_next(scenario: str = "auto"):
    if scenario not in ("auto", "dry", "wet"):
        raise HTTPException(400, "scenario must be auto, dry or wet")
    hit = _pred_cache.get(scenario)
    if hit and time.time() - hit[0] < 900:
        return hit[1]
    res = clean(predict.predict_next(scenario))
    _pred_cache[scenario] = (time.time(), res)
    return res


@app.get("/api/fanta")
def api_fanta(min_price: float | None = None):
    """Fanta F1 driver prices for the next race. min_price: 3.0-4.0, omitted = automatic."""
    lo, hi = fanta.MIN_PRICE_RANGE
    if min_price is not None and not lo <= min_price <= hi:
        raise HTTPException(400, f"min_price must be between {lo} and {hi}")
    nxt = api_next("auto")
    if not nxt.get("race"):
        return {"race": None}
    drivers = nxt["drivers"]
    values = [d["fanta_value"] for d in drivers]
    price, used = fanta.prices(values, min_price)
    rows = [{"driverId": d["driverId"], "name": d["name"], "code": d["code"], "team": d["team"],
             "teamId": d["teamId"], "value": d["fanta_value"], "value_quali": d["fanta_quali"],
             "value_race": d["fanta_race"], "value_model": d["fanta_model"], "form": d["fanta_form"],
             "form_n": d["fanta_form_n"], "price": float(p)} for d, p in zip(drivers, price)]
    rows.sort(key=lambda r: (-r["price"], -r["value"]))

    teams = {}
    for d in drivers:
        t = teams.setdefault(d["teamId"], {"teamId": d["teamId"], "name": d["team"], "drivers": [], "values": []})
        t["drivers"].append(d["code"] or d["name"])
        t["values"].append(d["fanta_value"])
    teams = list(teams.values())
    for t in teams:
        t["value"] = round(sum(t["values"]) / len(t["values"]), 2)
        del t["values"]
    for t, p in zip(teams, fanta.team_prices([t["value"] for t in teams], max(values))):
        t["price"] = float(p)
    teams.sort(key=lambda t: (-t["price"], -t["value"]))

    return clean({"race": nxt["race"], "top_price": fanta.TOP_PRICE, "min_price_range": [lo, hi],
                  "teams": teams, "team_price_range": list(fanta.TEAM_PRICE_RANGE),
                  "rules": {"budget": fanta.TOTAL_BUDGET,
                            "drivers": {"picks": fanta.DRIVER_PICKS}, "teams": {"picks": fanta.TEAM_PICKS}},
                  "min_price_auto": fanta.auto_min_price(values), "min_price": used, "drivers": rows,
                  "points_table": {"quali": fanta.QUALI_POINTS, "race": fanta.RACE_POINTS},
                  "calibration": fanta.load_calibration()})


@app.get("/api/seasons")
def api_seasons():
    t = tables()
    st, races = t["standings"], t["races"]
    out = []
    for season in sorted(races.season.unique(), reverse=True):
        champ_d = st[(st.season == season) & (st.kind == "driver") & (st.position == 1)]
        champ_c = st[(st.season == season) & (st.kind == "constructor") & (st.position == 1)]
        done = t["results"][t["results"].season == season]["round"].nunique()
        out.append({"season": int(season), "races": int((races.season == season).sum()),
                    "completed": int(done),
                    "driver": champ_d.name.iloc[0] if len(champ_d) else None,
                    "driverTeam": champ_d.team.iloc[0] if len(champ_d) else None,
                    "constructor": champ_c.name.iloc[0] if len(champ_c) else None})
    return clean(out)


@app.get("/api/season/{season}")
def api_season(season: int):
    t = tables()
    races = t["races"][t["races"].season == season]
    if races.empty:
        raise HTTPException(404, "season not found")
    res = t["results"][t["results"].season == season]
    w = t["weather"][t["weather"].season == season]
    bt = t["backtest"][t["backtest"].season == season]
    rows = []
    for race in races.sort_values("round").to_dict("records"):
        r = res[res["round"] == race["round"]]
        wr = w[w["round"] == race["round"]]
        b = bt[bt["round"] == race["round"]]
        win = r[r.position == 1]
        pole = r[r.grid == 1]
        item = {**race, "done": len(r) > 0,
                "winner": win.driverName.iloc[0] if len(win) else None,
                "winnerTeam": win.constructorName.iloc[0] if len(win) else None,
                "pole": pole.driverName.iloc[0] if len(pole) else None,
                "weather": wr.iloc[0].to_dict() if len(wr) else None}
        if len(b):
            names = r.set_index("driverId").driverName
            for m in ("pre_quali", "post_quali"):
                top = b[b[f"{m}_rank"] == 1]
                item[f"{m}_pick"] = names.get(top.driverId.iloc[0]) if len(top) else None
                item[f"{m}_hit"] = bool(len(top) and top.position.iloc[0] == 1)
        rows.append(item)
    st = t["standings"][t["standings"].season == season]
    return clean({
        "season": season, "races": rows,
        "drivers": records(st[st.kind == "driver"].sort_values("position")),
        "constructors": records(st[st.kind == "constructor"].sort_values("position")),
        "backtest": t["report"]["by_season"].get(str(season)),
    })


@app.get("/api/race/{season}/{rnd}")
def api_race(season: int, rnd: int):
    t = tables()
    race = t["races"][(t["races"].season == season) & (t["races"]["round"] == rnd)]
    if race.empty:
        raise HTTPException(404, "race not found")
    r = t["results"][(t["results"].season == season) & (t["results"]["round"] == rnd)]
    q = t["qualifying"][(t["qualifying"].season == season) & (t["qualifying"]["round"] == rnd)]
    b = t["backtest"][(t["backtest"].season == season) & (t["backtest"]["round"] == rnd)]
    r = r.merge(q[["driverId", "q_pos", "q_gap"]], on="driverId", how="left")
    cols = ["driverId", "pre_quali_rank", "pre_quali_pwin", "post_quali_rank", "post_quali_pwin"]
    r = r.merge(b[cols], on="driverId", how="left") if len(b) else r
    w = t["weather"][(t["weather"].season == season) & (t["weather"]["round"] == rnd)]
    return clean({"race": race.iloc[0].to_dict(),
                  "weather": w.iloc[0].to_dict() if len(w) else None,
                  "results": records(r.sort_values("position")),
                  "hasBacktest": len(b) > 0})


@app.get("/api/teams")
def api_teams():
    t = tables()
    res = t["results"]
    ts = team_season_table(res)
    latest = res.assign(family=res.constructorId.map(TEAM_FAMILY).fillna(res.constructorId)) \
        .sort_values(["season", "round"]).groupby("family").constructorName.last()
    ts["name"] = ts.family.map(latest)
    ts["lineage"] = ts.family.map(FAMILY_LABEL).fillna(ts.name)
    cs = t["standings"][t["standings"].kind == "constructor"].copy()
    cs["family"] = cs.id.map(TEAM_FAMILY).fillna(cs.id)
    n_races = t["races"].groupby("season").size()
    n_done = res.groupby("season")["round"].nunique()
    complete = {s for s in n_races.index if n_done.get(s, 0) >= n_races[s]}
    titles = cs[(cs.position == 1) & cs.season.isin(complete)].groupby("family").season.apply(list).to_dict()
    official = cs.groupby(["season", "family"]).agg(off_pos=("position", "min"),
                                                    off_points=("points", "sum")).reset_index()
    ts = ts.merge(official, on=["season", "family"], how="left")
    summary = ts.groupby("family").agg(name=("name", "first"), lineage=("lineage", "first"),
                                       seasons=("season", "nunique"), wins=("wins", "sum"),
                                       podiums=("podiums", "sum"), first=("season", "min"),
                                       last=("season", "max")).reset_index()
    summary["titles"] = summary.family.map(lambda f: titles.get(f, []))
    summary = summary.sort_values(["wins", "podiums"], ascending=False)
    return clean({"teams": records(summary),
                  "seasons": records(ts[["season", "family", "wins", "podiums", "points",
                                         "off_points", "off_pos", "share", "races"]])})


@app.get("/api/model")
def api_model():
    return clean(tables()["report"])


@app.get("/api/status")
def api_status():
    upd = DATA / "last_update.json"
    return clean({"refresh": _refresh,
                  "last_update": json.loads(upd.read_text()) if upd.exists() else None,
                  "trained_at": tables()["report"].get("trained_at")})


def _run_refresh():
    try:
        _refresh.update(running=True, error=None, step="Download dati F1 (Jolpica)")
        data_fetch.build()
        _refresh["step"] = "Tempi giro delle nuove gare (Jolpica)"
        race_pace.download(2018, 2100)
        _refresh["step"] = "Meteo (Open-Meteo)"
        weather_mod.build()
        _refresh["step"] = "Riaddestramento modelli"
        train.main()
        tables.cache_clear()
        _pred_cache.clear()
        _refresh.update(step="Completato", finished_at=time.strftime("%Y-%m-%d %H:%M"))
    except Exception as e:  # report to the UI
        traceback.print_exc()
        _refresh.update(error=str(e))
    finally:
        _refresh["running"] = False


@app.post("/api/refresh")
def api_refresh():
    if not _refresh["running"]:
        threading.Thread(target=_run_refresh, daemon=True).start()
    return JSONResponse({"started": True})


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
