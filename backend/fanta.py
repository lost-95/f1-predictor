"""Fanta F1 driver prices.

Rules:
  - the highest-valued driver costs exactly 30.0 coins
  - the lowest-valued driver costs between 3.0 and 4.0 coins
  - everyone else is placed proportionally between the two (linear in value)
  - prices have one decimal

Value = expected fanta points for the weekend (scoring tables below), as a blend of
  1) the models' forecast: expected qualifying points (qualifying-order model) + expected
     race points (pre-qualifying race model), simulated with temperatures fitted on fanta
     points rather than on the winner (the winner-fitted ones overrate the front runners
     and underrate the midfield);
  2) the driver's real average fanta points so far this season (form).
Form gets weight form_weight * n / (n + form_k) after n races. All parameters are chosen
on the 2010-2015 backtest and checked on 2016-today (see calibrate()). Everything is known
before qualifying, so prices stay fixed for the whole weekend.

Automatic minimum price: the last driver's purely proportional price (30 x its value /
the top value), kept inside 3.0-4.0. When it already falls in that range every price is
exactly proportional to value; otherwise the scale is anchored at the two ends.
"""
import json
import unicodedata

import numpy as np
import pandas as pd

import model as M
from config import DATA

TOP_PRICE = 30.0
MIN_PRICE_RANGE = (3.0, 4.0)

# Fanta points by position (index 0 = 1st place). Positions past the table score 0.
QUALI_POINTS = [149.82, 135.56, 122.66, 110.99, 99.43, 89.07, 79.00, 69.37, 59.71, 50.37, 43.36,
                36.95, 31.80, 27.37, 23.56, 20.07, 16.60, 13.59, 11.13, 9.11, 7.46, 6.11]
RACE_POINTS = [299.64, 271.13, 245.33, 221.98, 198.86, 178.14, 158.00, 138.74, 119.41, 100.74, 86.71,
               73.89, 63.60, 54.74, 47.11, 40.15, 33.20, 27.18, 22.26, 18.22, 14.92, 12.21]

# Team building rules: exactly 6 drivers and 3 teams, one shared budget for all of them.
# A team scores the points of both its drivers. TARGET_POINTS = a good squad score.
DRIVER_PICKS, TEAM_PICKS = 6, 3
TOTAL_BUDGET = 140.0
TARGET_POINTS = 2000

# Official prices given by the fantasy game (data/fanta_official_prices.json). While the
# file exists they replace the computed prices below (which stay available, just unused)
# and its driver list is the line-up of the next race (e.g. a newcomer replacing someone).
OFFICIAL_FILE = DATA / "fanta_official_prices.json"
TEAM_IDS = {
    "red bull racing": "red_bull", "red bull": "red_bull", "ferrari": "ferrari", "mercedes": "mercedes",
    "mclaren": "mclaren", "racing bulls": "rb", "rb f1 team": "rb", "alpine": "alpine",
    "alpine f1 team": "alpine", "audi": "audi", "haas f1 team": "haas", "haas": "haas",
    "aston martin": "aston_martin", "williams": "williams", "cadillac": "cadillac",
    "cadillac f1 team": "cadillac",
}
# Team price: average value of its drivers, on the same proportional scale as drivers
# (30 coins = value of the top driver), kept within 4-30. So 30 is reached only by a team
# whose drivers are both worth as much as the top driver.
TEAM_PRICE_RANGE = (4.0, 30.0)

CALIB_FILE = DATA / "fanta_calibration.json"
FIT_SEASONS, CHECK_FROM = (2010, 2015), 2016


# ---------- prices ----------
def auto_min_price(values) -> float:
    v = np.asarray(values, dtype=float)
    ratio = v.min() / v.max() if v.max() > 0 else 0.0
    return float(np.clip(round(TOP_PRICE * ratio, 1), *MIN_PRICE_RANGE))


def prices(values, min_price: float | None = None) -> tuple[np.ndarray, float]:
    """Return (price per driver, minimum price used)."""
    v = np.asarray(values, dtype=float)
    lo, hi = MIN_PRICE_RANGE
    p_min = auto_min_price(v) if min_price is None else round(float(np.clip(min_price, lo, hi)), 1)
    span = v.max() - v.min()
    if span <= 0:  # everyone identical: all at the top price
        return np.full(len(v), TOP_PRICE), p_min
    p = p_min + (TOP_PRICE - p_min) * (v - v.min()) / span
    return np.round(p, 1), p_min


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s))
    return " ".join("".join(c for c in s if not unicodedata.combining(c)).lower().split())


def team_id(name: str) -> str:
    n = _norm(name)
    return TEAM_IDS.get(n, n.replace(" ", "_"))


def load_official() -> dict | None:
    return json.loads(OFFICIAL_FILE.read_text(encoding="utf-8")) if OFFICIAL_FILE.exists() else None


def official_team_prices() -> dict[str, float]:
    off = load_official()
    return {team_id(t["name"]): float(t["price"]) for t in off["teams"]} if off else {}


def official_driver_price(name: str) -> float | None:
    """Official price for a driver name (full name, else unique surname; accents ignored)."""
    off = load_official()
    if not off:
        return None
    n = _norm(name)
    full = {_norm(d["name"]): d["price"] for d in off["drivers"]}
    if n in full:
        return float(full[n])
    surname = [d["price"] for d in off["drivers"] if _norm(d["name"]).split()[-1] == n.split()[-1]]
    return float(surname[0]) if len(surname) == 1 else None


def official_lineup(results: pd.DataFrame) -> list[dict] | None:
    """Drivers of the official price list as entrant rows. Names are matched to the results
    data (full name, else unique surname, accents ignored); a driver never seen before gets
    a new id."""
    off = load_official()
    if not off:
        return None
    recent = results[results.season >= results.season.max() - 1].drop_duplicates("driverId", keep="last")
    known = dict(zip(recent.driverId, recent.driverName))
    codes = dict(zip(recent.driverId, recent.driverCode))
    team_names = results.drop_duplicates("constructorId", keep="last").set_index("constructorId").constructorName
    rows = []
    for d in off["drivers"]:
        n = _norm(d["name"])
        did = next((k for k, full in known.items() if _norm(full) == n), None)
        if did is None:
            same_surname = [k for k, full in known.items() if _norm(full).split()[-1] == n.split()[-1]]
            did = same_surname[0] if len(same_surname) == 1 else None
        if did is None:  # newcomer
            surname = n.split()[-1]
            did, name, code = surname, d["name"], surname[:3].upper()
        else:
            name, code = known[did], codes.get(did)
        tid = team_id(d["team"])
        rows.append({"driverId": did, "driverName": name, "driverCode": code, "constructorId": tid,
                     "constructorName": team_names.get(tid, d["team"]), "price": float(d["price"])})
    return rows


def team_prices(team_values, top_driver_value: float) -> np.ndarray:
    """Computed team price (used without official prices). team_values: average driver value per team."""
    v = np.asarray(team_values, dtype=float)
    p = TOP_PRICE * v / top_driver_value if top_driver_value > 0 else np.full(len(v), TEAM_PRICE_RANGE[0])
    return np.round(np.clip(p, *TEAM_PRICE_RANGE), 1)


# ---------- real fanta points and season form ----------
def _lookup(table, pos: pd.Series) -> np.ndarray:
    t = np.array([0.0] + list(table))
    return t[np.clip(pos.fillna(len(table)).astype(int), 0, len(table))]


def realized(results: pd.DataFrame, quali: pd.DataFrame) -> pd.DataFrame:
    """Fanta points actually scored per driver and race (no qualifying time = last place)."""
    d = results[["season", "round", "driverId", "position"]].merge(
        quali[["season", "round", "driverId", "q_pos"]].drop_duplicates(["season", "round", "driverId"]),
        on=["season", "round", "driverId"], how="left")
    d["quali_fp"] = _lookup(QUALI_POINTS, d.q_pos)
    d["race_fp"] = _lookup(RACE_POINTS, d.position)
    d["fp"] = d.quali_fp + d.race_fp
    return d


def season_form(real: pd.DataFrame) -> pd.DataFrame:
    """Average real fanta points in the same season BEFORE each race, and how many races."""
    d = real.sort_values(["season", "round"]).copy()
    g = d.groupby(["season", "driverId"]).fp
    d["form_n"] = g.cumcount()
    d["form_avg"] = (g.cumsum() - d.fp) / d.form_n.replace(0, np.nan)
    return d[["season", "round", "driverId", "form_avg", "form_n"]]


def current_form(results: pd.DataFrame, quali: pd.DataFrame, season: int) -> pd.DataFrame:
    """Season-so-far average real fanta points per driver (for the next race)."""
    real = realized(results[results.season == season], quali)
    return real.groupby("driverId").fp.agg(form_avg="mean", form_n="count").reset_index()


def blend(model_value, form_avg, form_n, form_weight: float, form_k: float) -> np.ndarray:
    m = np.asarray(model_value, dtype=float)
    f = np.asarray(form_avg, dtype=float)
    n = np.nan_to_num(np.asarray(form_n, dtype=float))
    a = np.where(np.isnan(f), 0.0, form_weight * n / (n + form_k))
    return (1 - a) * m + a * np.nan_to_num(f)


def load_calibration() -> dict:
    if CALIB_FILE.exists():
        return json.loads(CALIB_FILE.read_text())
    return {"t_race": None, "t_quali": None, "form_weight": 0.0, "form_k": 1.0}


# ---------- calibration on the backtest ----------
def calibrate() -> dict:
    """Pick the fanta temperatures and the form blend on 2010-2015, check them on 2016-today."""
    bt = pd.read_csv(DATA / "backtest.csv")
    qb = pd.read_csv(DATA / "quali_backtest.csv")
    results, quali = pd.read_csv(DATA / "results.csv"), pd.read_csv(DATA / "qualifying.csv")
    rep = json.loads((DATA / "model_report.json").read_text())["models"]
    t_race0, t_quali0 = rep["pre_quali"]["temperature"], rep["quali"]["temperature"]

    d = bt[bt.season >= FIT_SEASONS[0]][["season", "round", "race_idx", "driverId", "pre_quali_score"]]
    d = d.merge(qb[["race_idx", "driverId", "score"]].rename(columns={"score": "q_score"}),
                on=["race_idx", "driverId"], how="left")
    d = d.merge(realized(results, quali)[["season", "round", "driverId", "quali_fp", "race_fp", "fp"]],
                on=["season", "round", "driverId"], how="left")
    d = d.merge(season_form(realized(results, quali)), on=["season", "round", "driverId"], how="left")
    d["q_score"] = d.q_score.fillna(d.groupby("race_idx").q_score.transform("min") - 1)
    d = d[d.q_score.notna()].sort_values(["race_idx", "driverId"]).reset_index(drop=True)
    groups = [g.index.values for _, g in d.groupby("race_idx")]
    fit = d.season.between(*FIT_SEASONS).values
    check = (d.season >= CHECK_FROM).values

    def expected(score_col, T, table):
        out = np.empty(len(d))
        for idx in groups:
            pos = M.simulate_positions(d.loc[idx, score_col].values, T, n_sims=3000, seed=0)
            out[idx] = M.expected_points(pos, table)
        return out

    def mse(pred, real, mask):
        return float(np.mean((pred[mask] - real[mask]) ** 2))

    grid = [0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.35, 1.5, 1.7, 2.0]
    race_by_t = {T: expected("pre_quali_score", T, RACE_POINTS) for T in grid + [t_race0]}
    quali_by_t = {T: expected("q_score", T, QUALI_POINTS) for T in grid + [t_quali0]}
    t_race = min(grid, key=lambda T: mse(race_by_t[T], d.race_fp.values, fit))
    t_quali = min(grid, key=lambda T: mse(quali_by_t[T], d.quali_fp.values, fit))

    base = race_by_t[t_race0] + quali_by_t[t_quali0]
    model = race_by_t[t_race] + quali_by_t[t_quali]
    real = d.fp.values
    options = [(w, k) for w in (0, .2, .3, .4, .5, .6, .7, .8, .9) for k in (0.5, 1, 2, 4)]
    blends = {o: blend(model, d.form_avg, d.form_n, *o) for o in options}
    w, k = min(options, key=lambda o: mse(blends[o], real, fit))
    final = blends[(w, k)]

    def by_rank(pred, mask):  # mean predicted vs real per predicted order
        x = d[mask].assign(pred=pred[mask])
        x["rank"] = x.groupby("race_idx").pred.rank(ascending=False, method="first").astype(int)
        t = x.groupby("rank")[["pred", "fp"]].mean().round(1)
        return {int(r): [float(a), float(b)] for r, (a, b) in t.loc[t.index <= 20].iterrows()}

    res = {
        "t_race": t_race, "t_quali": t_quali, "form_weight": w, "form_k": k,
        "fitted_on": f"{FIT_SEASONS[0]}-{FIT_SEASONS[1]}", "checked_on": f"{CHECK_FROM}-today",
        "check_rmse": {
            "winner_temperatures": round(mse(base, real, check) ** .5, 1),
            "fanta_temperatures": round(mse(model, real, check) ** .5, 1),
            "fanta_temperatures_plus_form": round(mse(final, real, check) ** .5, 1),
        },
        "check_by_rank": {"before": by_rank(base, check), "after": by_rank(final, check)},
    }
    CALIB_FILE.write_text(json.dumps(res, indent=2))
    return res


if __name__ == "__main__":
    r = calibrate()
    print({k: v for k, v in r.items() if k != "check_by_rank"})
    for name in ("before", "after"):
        t = r["check_by_rank"][name]
        print(name, {k: t[k] for k in list(t)[:14]})
