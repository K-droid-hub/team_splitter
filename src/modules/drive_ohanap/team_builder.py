"""
team_builder.py
----------------
coverage_score を使った貪欲法によるチーム提案。
"""

from __future__ import annotations
import os
import pandas as pd
from .coverage_score import coverage_score, _get_team_tags, DEFAULT_COVERAGE_CONFIG
from .game_history import load_weights
from .counter_rules import load_rules, analyze_opponent, get_active_boosts, counter_score
from .fetch import build_ja_index

ROLES_CSV = os.getenv("ROLES_CSV", "roles-test.csv")

# チーム戦 Tier → スコア乗数。最低 Tier (F) はほぼ選ばれないよう 0.05 に設定。
TIER_MULTIPLIER: dict[str, float] = {
    "S":   1.00,
    "A+":  0.90,
    "A":   0.80,
    "B+":  0.70,
    "B":   0.55,
    "C":   0.35,
    "D":   0.15,
    "F":   0.05,
    "TBD": 0.65,
}


def _tier_mult(ja_name: str, tier_index: dict) -> float:
    api_data = tier_index.get(ja_name)
    if api_data is None:
        return 0.65  # マッピング外は TBD 扱い
    return TIER_MULTIPLIER.get(api_data.get("tier", "TBD"), 0.65)


def _load_roles_df() -> pd.DataFrame:
    df = pd.read_csv(ROLES_CSV, encoding="shift_jis", index_col=0)
    df.index.name = "name"
    df = df.reset_index()
    return df[df["name"].notna() & (df["name"].str.strip() != "")]


def _compute_feature_scores(team: list[str], df: pd.DataFrame, config: dict) -> dict[str, float]:
    """チーム全体のカバレッジスコアを feature ごとに集計して返す。"""
    role_covered = _get_team_tags(team, df, config["role_tags"])
    tactic_covered = _get_team_tags(team, df, config["tactic_tags"])

    early_max = config.get("ps_early_max", 4)
    mid_max = config.get("ps_mid_max", 6)
    team_ps = df[df["name"].isin(team)]["PowerSpike"].dropna().tolist()
    bins = {"early": 0, "mid": 0, "late": 0}
    for ps in team_ps:
        v = int(ps)
        if v <= early_max:
            bins["early"] += 1
        elif v <= mid_max:
            bins["mid"] += 1
        else:
            bins["late"] += 1
    filled_bins = sum(1 for c in bins.values() if c > 0)

    return {
        "role": float(len(role_covered)),
        "tactic": float(len(tactic_covered)),
        "powerspike": float(filled_bins),
    }


def suggest_team(
    opponent_team: list[str],
    team_size: int = 5,
    config: dict | None = None,
) -> tuple[list[str], dict[str, float], dict[str, float]]:
    """
    相手チームに対して最適な自チームを貪欲法で提案する。

    Returns
    -------
    (suggested_team, feature_scores, weights_used)
    """
    df = _load_roles_df()

    if config is None:
        config = DEFAULT_COVERAGE_CONFIG.copy()
        saved = load_weights()
        if saved:
            config.update(saved)

    available = [n for n in df["name"].tolist() if n not in opponent_team]

    # カウンタールールを読み込み、発動するブーストを確定する
    rules = load_rules()
    opponent_attrs = analyze_opponent(opponent_team, df)
    active_boosts = get_active_boosts(rules, opponent_attrs)

    # Tier 情報を取得（API 通信は1回だけ）
    try:
        tier_index = build_ja_index()
    except Exception:
        tier_index = {}

    team: list[str] = []
    for _ in range(team_size):
        candidates = [c for c in available if c not in team]
        if not candidates:
            break
        best = max(
            candidates,
            key=lambda c: (
                coverage_score(c, team, df, config) + counter_score(c, df, active_boosts)
            ) * _tier_mult(c, tier_index),
        )
        team.append(best)

    feature_scores = _compute_feature_scores(team, df, config)
    weights_used = {
        "role_weight": config["role_weight"],
        "tactic_weight": config["tactic_weight"],
        "powerspike_weight": config["powerspike_weight"],
    }
    return team, feature_scores, weights_used
