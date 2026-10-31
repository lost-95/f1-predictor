"""Feature engineering. Every feature for a race uses only races that happened before it."""
import numpy as np
import pandas as pd

# Teams change names; history follows the factory (e.g. BAR -> Honda -> Brawn -> Mercedes)
TEAM_FAMILY = {
    "benetton": "enstone", "renault": "enstone", "lotus_f1": "enstone", "alpine": "enstone",
    "jaguar": "red_bull", "red_bull": "red_bull",
    "bar": "brackley", "honda": "brackley", "brawn": "brackley", "mercedes": "brackley",
    "minardi": "faenza", "toro_rosso": "faenza", "alphatauri": "faenza", "rb": "faenza",
    "jordan": "silverstone", "mf1": "silverstone", "spyker_mf1": "silverstone",
    "spyker": "silverstone", "force_india": "silverstone", "racing_point": "silverstone",
    "aston_martin": "silverstone",
    "sauber": "hinwil", "bmw_sauber": "hinwil", "alfa": "hinwil", "audi": "hinwil",
    "virgin": "banbury", "marussia": "banbury", "manor": "banbury",
    "lotus_racing": "hingham", "caterham": "hingham",
}
FAMILY_LABEL = {
    "enstone": "Benetton / Renault / Lotus / Alpine",
    "red_bull": "Jaguar / Red Bull",
    "brackley": "BAR / Honda / Brawn / Mercedes",
    "faenza": "Minardi / Toro Rosso / AlphaTauri / RB",
    "silverstone": "Jordan / Force India / Racing Point / Aston Martin",
    "hinwil": "Sauber / BMW Sauber / Alfa Romeo / Audi",
    "banbury": "Virgin / Marussia / Manor",
    "hingham": "Lotus Racing / Caterham",
}
# Seasons with a major technical-regulation reset (past form is less reliable)
REG_CHANGE = {2005, 2009, 2014, 2017, 2022, 2026}
POINTS = {1: 25, 2: 18, 3: 15, 4: 12, 5: 10, 6: 8, 7: 6, 8: 4, 9: 2, 10: 1}

FEATURES_BASE = [
    "d_avg_pos_3", "d_avg_pos_5", "d_avg_pos_10", "d_avg_grid_5", "d_pts_5", "d_dnf_10",
    "d_qgap_5", "d_races", "d_season_ppr", "d_champ_rank", "d_prev_rank", "d_circuit_avg", "d_circuit_n",
    "d_vs_mate_10", "d_wet_skill", "d_wet_adj",
    "t_avg_pos_5", "t_best_5", "t_pts_5", "t_season_ppr", "t_champ_rank", "t_prev_rank",
    "t_prev_share", "t_win_rate_3y", "t_podium_rate_3y", "t_titles", "t_circuit_avg",
    "t_dnf_10", "t_qgap_5",
    "c_grid_corr", "reg_change", "season_progress",
    "temp_c", "rain_mm", "pre_rain_mm", "humidity", "wind_kmh", "wet",
]
FEATURES_GRID = FEATURES_BASE + ["grid", "grid_vs_mate", "q_gap"]
FEATURES_ELO = ["d_elo", "t_elo", "elo", "elo_rank"]
FEATURES_PACE = ["d_rpace_3", "d_rpace_6", "t_rpace_5"]  # race pace from lap times (2018+)
# retirements for car failures: the result says nothing about pace
DRIVER_FAULT = {"Collision", "Accident", "Spun off", "Collision damage", "Disqualified"}


def is_finished(status: pd.Series) -> pd.Series:
    s = status.fillna("")
    return (s == "Finished") | s.str.startswith("+") | (s == "Lapped")


def _roll(df, key, col, n, func="mean"):
    g = df.groupby(key, sort=False)[col]
    shifted = g.shift(1)
    r = shifted.groupby(df[key]).rolling(n, min_periods=1)
    r = getattr(r, func)()
    return r.reset_index(level=0, drop=True)


def _expanding_mean(df, keys, col):
    shifted = df.groupby(keys, sort=False)[col].shift(1)
    grp = [df[k] for k in keys]
    s = shifted.fillna(0).groupby(grp).cumsum()
    n = shifted.notna().astype(float).groupby(grp).cumsum()
    return s / n.replace(0, np.nan), n


def add_elo(df: pd.DataFrame, k_driver: float = 32, k_team: float = 48) -> pd.DataFrame:
    """Pairwise Elo where a car's strength = driver rating + team rating.

    Ratings are recorded *before* each race, then updated from the finishing order of
    classified cars (mechanical retirements say little about pace). Teammates share the
    team rating, so their head-to-head only moves the driver ratings: this separates the
    driver from the car. Team ratings shrink toward the mean every winter, more strongly
    when the technical regulations change.
    """
    D, T = {}, {}
    pre_d = np.empty(len(df)); pre_t = np.empty(len(df))
    fin = (is_finished(df.status) & df.position.notna()).values
    last_season = None
    for race_idx, idx in df.groupby("race_idx", sort=True).indices.items():
        season = race_idx // 100
        if season != last_season:
            shrink_t = 0.4 if season in REG_CHANGE else 0.7
            T = {k: 1500 + (v - 1500) * shrink_t for k, v in T.items()}
            D = {k: 1500 + (v - 1500) * 0.9 for k, v in D.items()}
            last_season = season
        drivers = df.driverId.values[idx]; fams = df.family.values[idx]
        for j, i in enumerate(idx):
            pre_d[i] = D.setdefault(drivers[j], 1450.0)
            pre_t[i] = T.setdefault(fams[j], 1450.0)
        sel = [i for i in idx if fin[i]]
        if len(sel) < 2:
            continue
        pos = df.position.values[sel]
        strength = pre_d[sel] + pre_t[sel]
        exp = 1 / (1 + 10 ** ((strength[None, :] - strength[:, None]) / 400))  # P(i beats j)
        act = (pos[:, None] < pos[None, :]).astype(float)
        np.fill_diagonal(exp, 0); np.fill_diagonal(act, 0)
        err = (act - exp).sum(1) / (len(sel) - 1)
        for e, i in zip(err, sel):
            D[df.driverId.values[i]] += k_driver * e
        team_err = pd.Series(err).groupby(df.family.values[sel]).mean()
        for fam, e in team_err.items():
            T[fam] += k_team * e
    df["d_elo"], df["t_elo"] = pre_d, pre_t
    df["elo"] = pre_d + pre_t
    df["elo_rank"] = df.groupby("race_idx").elo.rank(ascending=False, method="min")
    return df


def team_season_table(results: pd.DataFrame) -> pd.DataFrame:
    """Per team family and season: races, wins, podiums, normalised points and rank."""
    r = results.copy()
    r["family"] = r.constructorId.map(TEAM_FAMILY).fillna(r.constructorId)
    r["npts"] = r.position.map(POINTS).fillna(0)
    t = r.groupby(["season", "family"]).agg(
        wins=("position", lambda p: int((p == 1).sum())),
        podiums=("position", lambda p: int((p <= 3).sum())),
        npts=("npts", "sum"),
        points=("points", "sum"),
    ).reset_index()
    t["races"] = t.season.map(r.groupby("season")["round"].nunique())
    t["rank"] = t.groupby("season").npts.rank(ascending=False, method="min")
    t["share"] = t.npts / t.groupby("season").npts.transform("sum")
    return t


def make_dataset(results: pd.DataFrame, races: pd.DataFrame, weather: pd.DataFrame,
                 quali: pd.DataFrame, upcoming: pd.DataFrame | None = None,
                 pace: pd.DataFrame | None = None) -> pd.DataFrame:
    """Return one row per (race, driver) with features and the target `position`.

    `upcoming` holds entrant rows (season, round, driverId, constructorId, ... grid optional)
    for a race not yet run; their `position` stays NaN.
    """
    df = results.copy()
    if upcoming is not None and len(upcoming):
        df = pd.concat([df, upcoming.assign(position=np.nan)], ignore_index=True)

    races = races.copy()
    races["race_idx"] = races.season * 100 + races["round"]
    races["n_rounds"] = races.groupby("season")["round"].transform("max")
    df = df.merge(races[["season", "round", "race_idx", "circuitId", "n_rounds", "date",
                         "raceName"]], on=["season", "round"], how="left")
    w = weather[["season", "round", "temp_c", "rain_mm", "pre_rain_mm", "humidity",
                 "wind_kmh", "wet"]]
    df = df.merge(w, on=["season", "round"], how="left")
    q = quali[["season", "round", "driverId", "q_gap", "q_pos"]].drop_duplicates(
        ["season", "round", "driverId"])
    df = df.drop(columns=[c for c in ("q_gap", "q_pos") if c in df]).merge(
        q, on=["season", "round", "driverId"], how="left")
    # race pace of *this* race is an outcome: it is only used through shifted rolling means
    if pace is not None and len(pace):
        p = pace[["season", "round", "driverId", "pace_gap"]].drop_duplicates(
            ["season", "round", "driverId"])
        df = df.merge(p, on=["season", "round", "driverId"], how="left")
    else:
        df["pace_gap"] = np.nan
    df = df.sort_values(["race_idx", "position"], na_position="last").reset_index(drop=True)

    df["family"] = df.constructorId.map(TEAM_FAMILY).fillna(df.constructorId)
    df["grid"] = df.grid.astype(float)
    df.loc[df.grid == 0, "grid"] = 22  # pit-lane start
    df["npts"] = df.position.map(POINTS)
    df.loc[df.position.notna(), "npts"] = df.loc[df.position.notna(), "npts"].fillna(0)
    df["dnf"] = np.where(df.position.isna(), np.nan, (~is_finished(df.status)).astype(float))
    df["gain"] = df.grid - df.position
    df["reg_change"] = df.season.isin(REG_CHANGE).astype(int)
    df["season_progress"] = df["round"] / df.n_rounds
    df = add_elo(df)

    # ---- driver form
    df["d_avg_pos_3"] = _roll(df, "driverId", "position", 3)
    df["d_avg_pos_5"] = _roll(df, "driverId", "position", 5)
    df["d_avg_pos_10"] = _roll(df, "driverId", "position", 10)
    df["d_avg_grid_5"] = _roll(df, "driverId", "grid", 5)
    df["d_pts_5"] = _roll(df, "driverId", "npts", 5)
    df["d_qgap_5"] = _roll(df, "driverId", "q_gap", 5)
    df["d_rpace_3"] = _roll(df, "driverId", "pace_gap", 3)
    df["d_rpace_6"] = _roll(df, "driverId", "pace_gap", 6)
    df["d_dnf_10"] = _roll(df, "driverId", "dnf", 10)
    df["d_races"] = df.groupby("driverId").cumcount()

    # season-to-date points and championship rank before the race
    g = df.groupby(["season", "driverId"])
    df["d_season_pts"] = g.npts.transform(lambda s: s.shift(1).cumsum()).fillna(0)
    df["d_season_n"] = g.cumcount()
    df["d_season_ppr"] = df.d_season_pts / df.d_season_n.replace(0, np.nan)
    df["d_champ_rank"] = df.groupby("race_idx").d_season_pts.rank(ascending=False, method="min")
    df.loc[df["round"] == 1, "d_champ_rank"] = np.nan

    drv_season = df[df.position.notna()].groupby(["season", "driverId"]).npts.sum().reset_index()
    drv_season["rank"] = drv_season.groupby("season").npts.rank(ascending=False, method="min")
    prev = dict(zip(zip(drv_season.season + 1, drv_season.driverId), drv_season["rank"]))
    df["d_prev_rank"] = [prev.get(k, np.nan) for k in zip(df.season, df.driverId)]

    df["d_circuit_avg"], df["d_circuit_n"] = _expanding_mean(df, ["driverId", "circuitId"], "position")

    # teammate comparison (same team, same race)
    df["mate_pos"] = (df.groupby(["race_idx", "constructorId"]).position.transform("sum") - df.position) / \
        (df.groupby(["race_idx", "constructorId"]).position.transform("count") - 1).replace(0, np.nan)
    df["vs_mate"] = df.position - df.mate_pos
    df["d_vs_mate_10"] = _roll(df, "driverId", "vs_mate", 10)
    mate_grid = (df.groupby(["race_idx", "constructorId"]).grid.transform("sum") - df.grid) / \
        (df.groupby(["race_idx", "constructorId"]).grid.transform("count") - 1).replace(0, np.nan)
    df["grid_vs_mate"] = df.grid - mate_grid

    # wet-weather skill: positions gained in wet races vs dry races (prior races only)
    wet = df.wet.fillna(0)
    for flag, name in ((1, "wet"), (0, "dry")):
        v = df.gain.where(wet == flag)
        tmp = df.assign(_v=v)
        df[f"d_{name}_gain"], _ = _expanding_mean(tmp, ["driverId"], "_v")
    df["d_wet_skill"] = df.d_wet_gain - df.d_dry_gain
    df["d_wet_adj"] = df.d_wet_skill.fillna(0) * wet

    # ---- team form (per race, then rolled over the team's races)
    tr = df.groupby(["race_idx", "family"], sort=True).agg(
        season=("season", "first"), t_mean=("position", "mean"), t_best=("position", "min"),
        t_npts=("npts", "sum"), t_dnf=("dnf", "mean"), t_qgap=("q_gap", "min"),
        t_rpace=("pace_gap", "min"),
        done=("position", lambda p: p.notna().any())).reset_index()
    tr.loc[~tr.done, ["t_mean", "t_best", "t_npts", "t_dnf"]] = np.nan
    tr["t_avg_pos_5"] = _roll(tr, "family", "t_mean", 5)
    tr["t_best_5"] = _roll(tr, "family", "t_best", 5)
    tr["t_pts_5"] = _roll(tr, "family", "t_npts", 5)
    tr["t_dnf_10"] = _roll(tr, "family", "t_dnf", 10)
    tr["t_qgap_5"] = _roll(tr, "family", "t_qgap", 5)
    tr["t_rpace_5"] = _roll(tr, "family", "t_rpace", 5)
    gs = tr.groupby(["season", "family"])
    tr["t_season_pts"] = gs.t_npts.transform(lambda s: s.shift(1).cumsum()).fillna(0)
    tr["t_season_ppr"] = tr.t_season_pts / gs.cumcount().replace(0, np.nan)
    tr["t_champ_rank"] = tr.groupby("race_idx").t_season_pts.rank(ascending=False, method="min")
    tr.loc[tr.race_idx % 100 == 1, "t_champ_rank"] = np.nan
    # team history at this circuit, computed per race so a car never sees its teammate's result
    tr = tr.merge(races[["race_idx", "circuitId"]], on="race_idx", how="left")
    tr["t_circuit_avg"], _ = _expanding_mean(tr, ["family", "circuitId"], "t_mean")
    df = df.merge(tr[["race_idx", "family", "t_avg_pos_5", "t_best_5", "t_pts_5", "t_dnf_10",
                      "t_season_ppr", "t_champ_rank", "t_circuit_avg", "t_qgap_5", "t_rpace_5"]],
                  on=["race_idx", "family"], how="left")

    # ---- long-term team success (the 25-year history)
    ts = team_season_table(results)
    ts_idx = ts.set_index(["season", "family"])
    titles = ts[ts["rank"] == 1].groupby("family").season.apply(sorted).to_dict()

    def hist(season, fam):
        p = ts_idx["rank"].get((season - 1, fam), np.nan)
        share = ts_idx["share"].get((season - 1, fam), np.nan)
        w = pod = n = 0
        for s in range(season - 3, season):
            if (s, fam) in ts_idx.index:
                row = ts_idx.loc[(s, fam)]
                w += row.wins; pod += row.podiums; n += row.races
        t = sum(1 for s in titles.get(fam, []) if s < season)
        return p, share, (w / n if n else np.nan), (pod / (2 * n) if n else np.nan), t

    keys = df[["season", "family"]].drop_duplicates()
    h = pd.DataFrame([(s, f, *hist(s, f)) for s, f in keys.itertuples(index=False)],
                     columns=["season", "family", "t_prev_rank", "t_prev_share", "t_win_rate_3y",
                              "t_podium_rate_3y", "t_titles"])
    df = df.merge(h, on=["season", "family"], how="left")

    # ---- circuit character: how much the grid decides the result here (prior races)
    fin = df[df.position.notna() & (df.dnf == 0)]
    corr = fin.groupby("race_idx").apply(
        lambda x: x.grid.corr(x.position, method="spearman") if len(x) > 4 else np.nan,
        include_groups=False).rename("race_grid_corr")
    rc = races[["race_idx", "circuitId"]].merge(corr, on="race_idx", how="left").sort_values("race_idx")
    rc["c_grid_corr"], _ = _expanding_mean(rc, ["circuitId"], "race_grid_corr")
    df = df.merge(rc[["race_idx", "c_grid_corr"]], on="race_idx", how="left")

    # final order must not depend on the result (ties would otherwise leak the finishing order)
    return df.sort_values(["race_idx", "driverId"]).reset_index(drop=True)
