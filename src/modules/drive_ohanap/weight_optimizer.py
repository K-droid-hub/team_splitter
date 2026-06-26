"""
weight_optimizer.py
--------------------
蓄積した試合データをもとに coverage_score の重みを自動調整する。

調整ロジック:
    各 feature（role/tactic/powerspike）について、
    勝ち試合の平均スコア vs 負け試合の平均スコアを比較し、
    勝ち試合で高い feature の weight を上げ、低い feature の weight を下げる。
"""

from __future__ import annotations
from statistics import mean
from .game_history import load_history, load_weights, save_weights
from .coverage_score import DEFAULT_COVERAGE_CONFIG

MIN_GAMES = 20       # 最適化を始める最低試合数
LEARNING_RATE = 0.05 # 1回の調整で weight を何割動かすか
MAX_WEIGHT = 10.0
MIN_WEIGHT = 0.1

_FEATURE_WEIGHT_MAP = [
    ("role",       "role_weight"),
    ("tactic",     "tactic_weight"),
    ("powerspike", "powerspike_weight"),
]


def optimize_weights() -> dict | None:
    """
    試合履歴から重みを調整して保存する。
    データ不足（MIN_GAMES 未満）なら None を返す。
    """
    labeled = [g for g in load_history() if g["result"] in ("win", "loss")]
    if len(labeled) < MIN_GAMES:
        return None

    wins   = [g for g in labeled if g["result"] == "win"]
    losses = [g for g in labeled if g["result"] == "loss"]
    if not wins or not losses:
        return None

    current = load_weights() or {k: DEFAULT_COVERAGE_CONFIG[k] for _, k in _FEATURE_WEIGHT_MAP}
    new_weights = current.copy()

    for feature, weight_key in _FEATURE_WEIGHT_MAP:
        win_avg  = mean(g["feature_scores"].get(feature, 0.0) for g in wins)
        loss_avg = mean(g["feature_scores"].get(feature, 0.0) for g in losses)

        if win_avg > loss_avg:
            new_weights[weight_key] = min(current[weight_key] * (1 + LEARNING_RATE), MAX_WEIGHT)
        elif win_avg < loss_avg:
            new_weights[weight_key] = max(current[weight_key] * (1 - LEARNING_RATE), MIN_WEIGHT)

    save_weights(new_weights)
    return new_weights


def get_stats() -> dict:
    """試合統計を返す（表示・デバッグ用）。"""
    history = load_history()
    labeled = [g for g in history if g["result"] in ("win", "loss")]
    wins    = sum(1 for g in labeled if g["result"] == "win")
    losses  = sum(1 for g in labeled if g["result"] == "loss")
    pending = sum(1 for g in history if g["result"] is None)
    return {
        "total":    len(labeled),
        "wins":     wins,
        "losses":   losses,
        "pending":  pending,
        "win_rate": wins / len(labeled) if labeled else 0.0,
    }
