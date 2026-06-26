"""
counter_rules.py
-----------------
相手チームの属性を分析し、counter_rules.yaml のルールに基づいてスコアを加算する。
"""

from __future__ import annotations
import os
import re
import yaml
import pandas as pd

RULES_PATH = os.getenv("COUNTER_RULES", "counter_rules.yaml")


def load_rules(path: str = RULES_PATH) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("rules", []) if data else []


def analyze_opponent(opponent_team: list[str], df: pd.DataFrame) -> dict[str, int]:
    """相手チームの各属性の保有数を返す（0の属性は含まない）。"""
    rows = df[df["name"].isin(opponent_team)]
    result = {}
    for col in df.columns:
        if col == "name":
            continue
        count = int((rows[col] == 1).sum())
        if count > 0:
            result[col] = count
    return result


def _eval_condition(actual: int, condition: str | int) -> bool:
    if isinstance(condition, int):
        return actual >= condition
    m = re.match(r"^(>=|<=|>|<|==)\s*(\d+)$", str(condition).strip())
    if not m:
        return False
    op, val = m.group(1), int(m.group(2))
    return {">=": actual >= val, "<=": actual <= val,
            ">" : actual > val,  "<" : actual < val,
            "==": actual == val}[op]


def _resolve_actual(key: str, opponent_attrs: dict[str, int]) -> int:
    """
    key に | が含まれる場合は複数タグの合算を返す。
    例: "Shield|ShieldSupport" → Shield の数 + ShieldSupport の数
    """
    tags = [t.strip() for t in key.split("|")]
    return sum(opponent_attrs.get(t, 0) for t in tags)


# active_boosts の要素: (tag_list, pokemon_list, weight)
# tag_list    → CSV の列名にマッチするタグ
# pokemon_list → ポケモン名に直接マッチ
ActiveBoost = tuple[list[str], list[str], float]


def get_active_boosts(
    rules: list[dict],
    opponent_attrs: dict[str, int],
) -> list[ActiveBoost]:
    """
    発動したルールから (対象タグリスト, 対象ポケモンリスト, weight) のリストを返す。
    """
    active: list[ActiveBoost] = []
    for rule in rules:
        conditions = rule.get("if_opponent", {})
        if all(_eval_condition(_resolve_actual(key, opponent_attrs), cond) for key, cond in conditions.items()):
            boost = rule.get("boost")
            if boost:
                tags    = boost.get("tags", []) + boost.get("roles", [])
                pokemon = boost.get("pokemon", [])
                weight  = float(boost.get("weight", 1.0))
                if tags or pokemon:
                    active.append((tags, pokemon, weight))
    return active


def counter_score(
    candidate: str,
    df: pd.DataFrame,
    active_boosts: list[ActiveBoost],
) -> float:
    """候補ポケモンのカウンタースコアを返す。"""
    rows = df[df["name"] == candidate]
    if rows.empty:
        return 0.0
    row = rows.iloc[0]
    score = 0.0
    for tags, pokemon_list, weight in active_boosts:
        for tag in tags:
            if tag in row.index and row[tag] == 1:
                score += weight
        if candidate in pokemon_list:
            score += weight
    return score
