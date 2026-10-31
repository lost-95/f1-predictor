"""Prediction for the next Grand Prix."""
from datetime import date, timedelta

import numpy as np
import pandas as pd

import fanta
import model as M
from config import DATA
from data_fetch import add_quali_gap, fetch_qualifying, quali_row
from features import FAMILY_LABEL, TEAM_FAMILY, make_dataset
from weather import weather_for_race

SCENARIOS = {
    "dry": {"rain_mm": 0.0, "pre_rain_mm": 0.0, "wet": 0, "humidity": 45.0},
    "wet": {"rain_mm": 3.0, "pre_rain_mm": 2.0, "wet": 1, "humidity": 92.0},
}


def load_tables():
    return (pd.read_csv(DATA / "races.csv"), pd.read_csv(DATA / "results.csv"),
            pd.read_csv(DATA / "weather.csv"), pd.read_csv(DATA / "qualifying.csv"))


def next_race(races: pd.DataFrame, results: pd.DataFrame) -> dict | None:
    done = set(zip(results.season, results["round"]))
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    todo = races[[(s, r) not in done for s, r in zip(races.season, races["round"])]]
    todo = todo[todo.date >= yesterday].sort_values(["season", "round"])
    return todo.iloc[0].to_dict() if len(todo) else None


def entrants(race: dict, results: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """Entry list: from qualifying if it has happened, else from the latest race."""
    season, rnd = int(race["season"]), int(race["round"])
    try:
        q = fetch_qualifying(season, rnd)
    except Exception:
        q = []
    if q:
        quali = add_quali_gap(pd.DataFrame([quali_row(season, rnd, x) for x in q]))
        ent = quali.assign(grid=quali.q_pos.astype(float))[
            ["season", "round", "driverId", "driverName", "driverCode", "constructorId",
             "constructorName", "grid"]]
        names = results.drop_duplicates("constructorId", keep="last").set_index("constructorId").constructorName
        ent["constructorName"] = ent.constructorId.map(names).fillna(ent.constructorName)
        return ent, quali, "post_quali"
    lineup = fanta.official_lineup(results)  # official fantasy list = latest known line-up
    if lineup:
        ent = pd.DataFrame(lineup).drop(columns="price")
    else:
        cur = results[results.season == results.season.max()]
        last = cur[cur["round"] == cur["round"].max()]
        ent = last[["driverId", "driverName", "driverCode", "constructorId", "constructorName"]].copy()
    ent["season"], ent["round"], ent["grid"] = season, rnd, np.nan
    return ent, pd.DataFrame(), "pre_quali"


def predict_next(scenario: str = "auto") -> dict:
    races, results, weather, quali = load_tables()
    race = next_race(races, results)
    if race is None:
        return {"race": None}
    ent, quali_new, mode = entrants(race, results)

    w = weather_for_race(race)
    forecast = dict(w)
    if scenario in SCENARIOS:
        w = {**w, **SCENARIOS[scenario]}
        w.setdefault("temp_c", 22.0); w.setdefault("wind_kmh", 10.0)
    weather = weather[~((weather.season == race["season"]) & (weather["round"] == race["round"]))]
    weather = pd.concat([weather, pd.DataFrame([w])], ignore_index=True)
    if len(quali_new):
        quali = pd.concat([quali, quali_new], ignore_index=True)

    pace_file = DATA / "race_pace.csv"
    pace = pd.read_csv(pace_file) if pace_file.exists() else None
    df = make_dataset(results, races, weather, quali, ent, pace=pace)
    rows = df[df.race_idx == race["season"] * 100 + race["round"]].reset_index(drop=True)

    out_models = {}
    for name in (["post_quali", "pre_quali"] if mode == "post_quali" else ["pre_quali"]):
        boosters, meta = M.load(name)
        scores = M.predict_ensemble(boosters, rows[meta["features"]])
        probs = M.race_probabilities(scores, meta["temperature"])
        out_models[name] = {"scores": scores, **probs}

    # Fanta F1: expected weekend points (all known before qualifying) blended with season form
    cal = fanta.load_calibration()
    pre_boosters, pre_meta = M.load("pre_quali")
    race_pos = M.simulate_positions(out_models["pre_quali"]["scores"],
                                    cal["t_race"] or pre_meta["temperature"], seed=2)
    fanta_race = M.expected_points(race_pos, fanta.RACE_POINTS)
    q_boosters, q_meta = M.load("quali")
    q_pos = M.simulate_positions(M.predict_ensemble(q_boosters, rows[q_meta["features"]]),
                                 cal["t_quali"] or q_meta["temperature"], seed=1)
    fanta_quali = M.expected_points(q_pos, fanta.QUALI_POINTS)
    form = rows[["driverId"]].merge(fanta.current_form(results, quali, int(race["season"])),
                                    on="driverId", how="left")
    fanta_model = fanta_quali + fanta_race
    fanta_value = fanta.blend(fanta_model, form.form_avg, form.form_n, cal["form_weight"], cal["form_k"])

    primary = out_models[mode]
    drivers = []
    for i, r in rows.iterrows():
        fam = TEAM_FAMILY.get(r.constructorId, r.constructorId)
        d = {
            "driverId": r.driverId, "name": r.driverName, "code": r.driverCode,
            "team": r.constructorName, "teamId": r.constructorId,
            "teamFamily": FAMILY_LABEL.get(fam, r.constructorName),
            "grid": None if pd.isna(r.grid) else int(r.grid),
            "q_gap": None if pd.isna(r.get("q_gap")) else round(float(r.q_gap), 3),
            "p_win": round(float(primary["p_win"][i]), 4),
            "p_podium": round(float(primary["p_podium"][i]), 4),
            "p_points": round(float(primary["p_points"][i]), 4),
            "exp_pos": round(float(primary["exp_pos"][i]), 2),
            "exp_points": round(float(primary["exp_points"][i]), 2),
            "fanta_quali": round(float(fanta_quali[i]), 2),
            "fanta_race": round(float(fanta_race[i]), 2),
            "fanta_model": round(float(fanta_model[i]), 2),
            "fanta_form": None if pd.isna(form.form_avg[i]) else round(float(form.form_avg[i]), 2),
            "fanta_form_n": int(np.nan_to_num(form.form_n[i])),
            "fanta_value": round(float(fanta_value[i]), 2),
            "stats": {k: (None if pd.isna(r[k]) else round(float(r[k]), 2)) for k in (
                "d_avg_pos_5", "d_season_ppr", "d_circuit_avg", "d_circuit_n", "d_wet_skill",
                "d_qgap_5", "t_pts_5", "t_prev_rank", "t_win_rate_3y", "t_titles", "d_champ_rank",
                "d_elo", "t_elo", "d_rpace_6")},
        }
        if "pre_quali" in out_models and mode == "post_quali":
            d["pre_p_win"] = round(float(out_models["pre_quali"]["p_win"][i]), 4)
            d["pre_exp_pos"] = round(float(out_models["pre_quali"]["exp_pos"][i]), 2)
        drivers.append(d)
    drivers.sort(key=lambda d: (-d["p_win"], d["exp_pos"]))
    for k, d in enumerate(sorted(drivers, key=lambda d: d["exp_pos"]), 1):
        d["pred_pos"] = k
    drivers.sort(key=lambda d: d["pred_pos"])

    _, meta = M.load(mode)
    return {
        "race": {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in race.items()},
        "mode": mode,
        "scenario": scenario,
        "weather": {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in forecast.items()},
        "drivers": drivers,
        "model": {"name": mode, "metrics": meta["metrics"], "baseline": meta["baseline"],
                  "baseline_metrics": meta["baseline_metrics"]},
    }


if __name__ == "__main__":
    import json
    res = predict_next()
    print(res["race"]["raceName"], res["mode"], res["weather"])
    for d in res["drivers"][:10]:
        print(f'{d["pred_pos"]:2d} {d["name"]:25s} {d["team"]:18s} grid={d["grid"]} '
              f'win={d["p_win"]:.1%} podium={d["p_podium"]:.1%} exp={d["exp_pos"]}')
