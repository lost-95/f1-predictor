"""LightGBM learning-to-rank model + Plackett-Luce probabilities."""
import json

import lightgbm as lgb
import numpy as np
import pandas as pd

from config import MODELS

# Chosen by walk-forward comparison over 2016-2026 (small trees; gain focused on the top 10)
N_ESTIMATORS = 200
PARAMS = dict(
    objective="lambdarank", learning_rate=0.03, num_leaves=7, min_child_samples=80,
    subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0,
    label_gain=[0] * 11 + [1, 2, 3, 4, 5, 6, 8, 10, 13, 18, 25] + [30] * 10, verbose=-1,
)


def relevance(position: pd.Series) -> np.ndarray:
    return np.clip(21 - position.values, 0, 30).astype(int)


def group_sizes(df: pd.DataFrame) -> np.ndarray:
    return df.groupby("race_idx", sort=False).size().values


def training_rows(train: pd.DataFrame, drop_dnf: str | None) -> pd.DataFrame:
    """drop_dnf: None keeps every row, 'mech' drops car-failure retirements, 'all' drops all retirements."""
    if drop_dnf is None:
        return train
    from features import DRIVER_FAULT, is_finished
    fin = is_finished(train.status)
    if drop_dnf == "all":
        return train[fin]
    return train[fin | train.status.isin(DRIVER_FAULT)]


def recency_weights(train: pd.DataFrame, half_life: float | None) -> np.ndarray | None:
    """Weight per row halving every `half_life` seasons back from the latest training season."""
    if not half_life:
        return None
    age = train.season.max() - train.season
    return (0.5 ** (age / half_life)).values


def fit(train: pd.DataFrame, features: list[str], n_estimators: int,
        valid: pd.DataFrame | None = None, half_life: float | None = None,
        drop_dnf: str | None = None, params: dict | None = None) -> lgb.LGBMRanker:
    train = training_rows(train, drop_dnf).sort_values("race_idx", kind="stable")
    m = lgb.LGBMRanker(n_estimators=n_estimators, **(params or PARAMS))
    w = recency_weights(train, half_life)
    kw = {}
    if valid is not None:
        valid = valid.sort_values("race_idx")
        kw = dict(eval_set=[(valid[features], relevance(valid.position))],
                  eval_group=[group_sizes(valid)], eval_at=[5],
                  callbacks=[lgb.early_stopping(200, verbose=False)])
    m.fit(train[features], relevance(train.position), group=group_sizes(train),
          sample_weight=w, **kw)
    return m


def winner_logloss(df: pd.DataFrame, scores: np.ndarray, T: float) -> float:
    d = df.assign(z=scores / T)
    d["p"] = np.exp(d.z - d.groupby("race_idx").z.transform("max"))
    d["p"] /= d.groupby("race_idx").p.transform("sum")
    return float(-np.log(d.loc[d.position == 1, "p"].clip(1e-9)).mean())


def fit_temperature(df: pd.DataFrame, scores: np.ndarray) -> float:
    grid = np.exp(np.linspace(np.log(0.01), np.log(10), 200))
    return float(min(grid, key=lambda T: winner_logloss(df, scores, T)))


F1_POINTS = np.array([25, 18, 15, 12, 10, 8, 6, 4, 2, 1])


def simulate_positions(scores: np.ndarray, T: float, n_sims: int = 20000, seed: int = 0) -> np.ndarray:
    """Plackett-Luce via the Gumbel trick: (n_sims, n_drivers) matrix of finishing positions."""
    rng = np.random.default_rng(seed)
    n = len(scores)
    noisy = scores[None, :] / T + rng.gumbel(size=(n_sims, n))
    order = np.argsort(-noisy, axis=1)
    pos = np.empty_like(order)
    pos[np.arange(n_sims)[:, None], order] = np.arange(1, n + 1)
    return pos


def expected_points(pos: np.ndarray, points_by_position) -> np.ndarray:
    """Mean points per driver given simulated positions; positions past the table score 0."""
    n = pos.max()
    table = np.zeros(n + 1)
    pts = np.asarray(points_by_position, dtype=float)[:n]
    table[1:len(pts) + 1] = pts
    return table[pos].mean(0)


def race_probabilities(scores: np.ndarray, T: float, n_sims: int = 20000, seed: int = 0) -> dict:
    """P(win), P(podium), P(points), expected position and expected championship points."""
    pos = simulate_positions(scores, T, n_sims, seed)
    return {
        "p_win": (pos == 1).mean(0),
        "p_podium": (pos <= 3).mean(0),
        "p_points": (pos <= 10).mean(0),
        "exp_pos": pos.mean(0),
        "exp_points": expected_points(pos, F1_POINTS),
        "positions": pos,
    }


SEEDS = [0, 1, 2, 3, 4]  # 5 models averaged: lower variance than a single one


def fit_ensemble(train: pd.DataFrame, features: list[str], drop_dnf: str | None = None,
                 half_life: float | None = None) -> list[lgb.LGBMRanker]:
    return [fit(train, features, N_ESTIMATORS, half_life=half_life, drop_dnf=drop_dnf,
                params=dict(PARAMS, random_state=s)) for s in SEEDS]


def predict_ensemble(models, X: pd.DataFrame) -> np.ndarray:
    return np.mean([m.predict(X) for m in models], axis=0)


def save(models: list[lgb.LGBMRanker], name: str, meta: dict):
    for old in MODELS.glob(f"{name}*.txt"):
        old.unlink()
    for i, m in enumerate(models):
        m.booster_.save_model(str(MODELS / f"{name}_{i}.txt"))
    (MODELS / f"{name}.json").write_text(json.dumps(meta, indent=2))


def load(name: str) -> tuple[list[lgb.Booster], dict]:
    meta = json.loads((MODELS / f"{name}.json").read_text())
    boosters = [lgb.Booster(model_file=str(p)) for p in sorted(MODELS.glob(f"{name}_*.txt"))]
    return boosters, meta
