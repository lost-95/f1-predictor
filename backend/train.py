"""Train, evaluate and backtest the race-result models.

Two models:
  pre_quali  - before qualifying (no grid / qualifying gap)
  post_quali - after qualifying (uses the starting grid and the gap to pole)

Evaluation is walk-forward: every season S is predicted by a model trained only on
seasons before S. Those out-of-sample predictions are the backtest shown in the web
app, and they are also used to calibrate the probabilities (temperature).

A third model predicts the qualifying order (for the Fanta F1 qualifying points).

Usage:  python backend/train.py          (everything)
        python backend/train.py quali    (only the qualifying model)
"""
import json
from datetime import datetime

import numpy as np
import pandas as pd

import model as M
from config import DATA
from features import FEATURES_BASE, FEATURES_ELO, FEATURES_GRID, FEATURES_PACE, make_dataset

# Chosen by a 5-seed walk-forward comparison (2016-2026). Elo ratings and dropping
# car-failure retirements from training improved the post-qualifying probabilities;
# neither helped the pre-qualifying model, nor did recency weighting.
# Race pace from lap times (2018+, tested on 2019-2026) helped the pre-qualifying model
# slightly but made the post-qualifying probabilities worse, so only pre uses it.
MODELS_DEF = {
    "pre_quali": {"features": FEATURES_BASE + FEATURES_PACE, "drop_dnf": None},
    "post_quali": {"features": FEATURES_GRID + FEATURES_ELO, "drop_dnf": "mech"},
}
BASELINE = {"pre_quali": "standings", "post_quali": "grid"}
FIRST_BACKTEST = 2004

# Qualifying-order model (used for the Fanta F1 qualifying points). Same features as the
# pre-qualifying race model; the target is the qualifying position instead of the result.
QUALI_FEATURES = FEATURES_BASE + FEATURES_PACE
QUALI_FIRST_BACKTEST = 2010


def load_pace() -> pd.DataFrame | None:
    f = DATA / "race_pace.csv"
    return pd.read_csv(f) if f.exists() else None


def load_dataset() -> pd.DataFrame:
    results = pd.read_csv(DATA / "results.csv")
    races = pd.read_csv(DATA / "races.csv")
    weather = pd.read_csv(DATA / "weather.csv")
    quali = pd.read_csv(DATA / "qualifying.csv")
    df = make_dataset(results, races, weather, quali, pace=load_pace())
    starters = df[df.grid < 22].groupby("race_idx").size()
    df = df[df.race_idx.isin(starters[starters >= 12].index)]  # drops e.g. USA 2005 (6 cars)
    return df


def evaluate(df: pd.DataFrame, scores: np.ndarray, T: float | None) -> dict:
    d = df.assign(score=scores)
    d["pred_rank"] = d.groupby("race_idx").score.rank(ascending=False, method="first")
    winner_hit, podium_overlap, spearman, mae = [], [], [], []
    for _, r in d.groupby("race_idx"):
        winner_hit.append(float(r.loc[r.pred_rank == 1, "position"].iloc[0] == 1))
        podium_overlap.append(len(set(r[r.pred_rank <= 3].driverId) & set(r[r.position <= 3].driverId)) / 3)
        spearman.append(r.pred_rank.corr(r.position, method="spearman"))
        mae.append((r.pred_rank - r.position).abs().mean())
    return {
        "races": int(d.race_idx.nunique()),
        "winner_accuracy": round(float(np.mean(winner_hit)), 3),
        "podium_overlap": round(float(np.mean(podium_overlap)), 3),
        "spearman": round(float(np.nanmean(spearman)), 3),
        "position_mae": round(float(np.mean(mae)), 2),
        "winner_logloss": round(M.winner_logloss(df, scores, T), 3) if T else None,
    }


def baseline_scores(df: pd.DataFrame, kind: str) -> np.ndarray:
    if kind == "grid":  # starting order = finishing order
        return -df.grid.values
    # championship order before the race (falls back to last season's rank)
    return -df.d_champ_rank.fillna(df.d_prev_rank).fillna(30).values


def train_quali(df: pd.DataFrame) -> dict:
    """Walk-forward check + final ensemble for the qualifying-order model. Returns its meta."""
    q = df[df.q_pos.notna()].assign(position=lambda d: d.q_pos)
    feats = QUALI_FEATURES
    parts = []
    for season in range(QUALI_FIRST_BACKTEST, int(q.season.max()) + 1):
        tr, te = q[q.season < season], q[q.season == season]
        if len(te):
            parts.append(te.assign(score=M.fit(tr, feats, M.N_ESTIMATORS).predict(te[feats])))
    bt = pd.concat(parts)
    bt[["season", "round", "race_idx", "driverId", "q_pos", "score"]].to_csv(
        DATA / "quali_backtest.csv", index=False)
    T = M.fit_temperature(bt, bt.score.values)
    recent = bt[bt.season >= 2016]
    metrics = {"2016-now": evaluate(recent, recent.score.values, T)}
    # reference: order by average qualifying gap over the last 5 races
    base = {"2016-now": evaluate(recent, -recent.d_qgap_5.fillna(10).values, None)}
    final = M.fit_ensemble(q, feats)
    imp = pd.Series(np.mean([m.booster_.feature_importance("gain") for m in final], axis=0), index=feats)
    imp = (imp / imp.sum()).sort_values(ascending=False).round(4)
    meta = {"features": feats, "n_estimators": M.N_ESTIMATORS, "temperature": T, "ensemble": len(final),
            "target": "qualifying position", "metrics": metrics, "baseline": "recent qualifying gap",
            "baseline_metrics": base, "importance": imp.to_dict()}
    M.save(final, "quali", meta)
    print(f"\n[quali] T={T:.3f}\n  model   : {metrics['2016-now']}\n  baseline: {base['2016-now']}")
    return meta


def main():
    df = load_dataset()
    print(f"Dataset: {len(df)} rows, {df.race_idx.nunique()} races")

    # 1) walk-forward backtest
    parts = []
    for season in range(FIRST_BACKTEST, int(df.season.max()) + 1):
        tr, te = df[df.season < season], df[df.season == season]
        out = te[["season", "round", "race_idx", "driverId", "position", "grid"]].copy()
        for name, cfg in MODELS_DEF.items():
            models = M.fit_ensemble(tr, cfg["features"], drop_dnf=cfg["drop_dnf"])
            out[f"{name}_score"] = M.predict_ensemble(models, te[cfg["features"]])
        parts.append(out)
        print(f"backtest {season}", flush=True)
    bt = pd.concat(parts)
    bt = bt.merge(df[["race_idx", "driverId", "d_champ_rank", "d_prev_rank"]],
                  on=["race_idx", "driverId"], how="left")

    report = {"trained_at": datetime.now().isoformat(timespec="seconds"),
              "method": "walk-forward: each season predicted by a model trained on earlier seasons",
              "n_races": int(df.race_idx.nunique()), "n_rows": int(len(df)), "models": {}}
    recent = bt[bt.season >= 2016]
    last2 = bt[bt.season >= int(df.season.max()) - 1]
    for name, cfg in MODELS_DEF.items():
        feats = cfg["features"]
        T = M.fit_temperature(bt[bt.season >= 2010], bt.loc[bt.season >= 2010, f"{name}_score"].values)
        z = bt[f"{name}_score"] / T
        p = np.exp(z - z.groupby(bt.race_idx).transform("max"))
        bt[f"{name}_pwin"] = (p / p.groupby(bt.race_idx).transform("sum")).round(4)
        bt[f"{name}_rank"] = bt.groupby("race_idx")[f"{name}_score"].rank(
            ascending=False, method="first").astype(int)

        metrics = {
            "2016-now": evaluate(recent, recent[f"{name}_score"].values, T),
            "last 2 seasons": evaluate(last2, last2[f"{name}_score"].values, T),
        }
        base = {
            "2016-now": evaluate(recent, baseline_scores(recent, BASELINE[name]), None),
            "last 2 seasons": evaluate(last2, baseline_scores(last2, BASELINE[name]), None),
        }
        final = M.fit_ensemble(df, feats, drop_dnf=cfg["drop_dnf"])
        imp = pd.Series(np.mean([m.booster_.feature_importance("gain") for m in final], axis=0),
                        index=feats)
        imp = (imp / imp.sum()).sort_values(ascending=False).round(4)
        meta = {"features": feats, "n_estimators": M.N_ESTIMATORS, "temperature": T,
                "drop_dnf": cfg["drop_dnf"], "ensemble": len(final),
                "metrics": metrics, "baseline": BASELINE[name], "baseline_metrics": base,
                "importance": imp.to_dict()}
        M.save(final, name, meta)
        report["models"][name] = meta
        print(f"\n[{name}] T={T:.3f}")
        for k in metrics:
            print(f"  {k:15s} model   : {metrics[k]}")
            print(f"  {k:15s} baseline: {base[k]}")

    report["models"]["quali"] = train_quali(df)
    report["by_season"] = {
        int(s): {name: evaluate(g, g[f"{name}_score"].values, report["models"][name]["temperature"])
                 for name in MODELS_DEF} | {
                 "grid_baseline": evaluate(g, baseline_scores(g, "grid"), None)}
        for s, g in bt.groupby("season")}
    bt.drop(columns=["d_champ_rank", "d_prev_rank"]).to_csv(DATA / "backtest.csv", index=False)
    (DATA / "model_report.json").write_text(json.dumps(report, indent=2))
    print("\nSaved models, backtest and report.")
    import fanta
    cal = fanta.calibrate()
    print("Fanta calibration:", {k: cal[k] for k in ("t_race", "t_quali", "form_weight", "form_k", "check_rmse")})


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["quali"]:  # retrain only the qualifying model
        rep_file = DATA / "model_report.json"
        rep = json.loads(rep_file.read_text())
        rep["models"]["quali"] = train_quali(load_dataset())
        rep_file.write_text(json.dumps(rep, indent=2))
        import fanta
        fanta.calibrate()
    else:
        main()
