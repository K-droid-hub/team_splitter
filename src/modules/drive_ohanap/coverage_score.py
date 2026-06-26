"""
coverage_score.py
------------------
チームのタグカバレッジ（役割・戦術特性の網羅率）を評価するモジュール。

設計思想:
    マルチスタート貪欲法の各ステップで「次の1体を追加したとき、
    チーム全体のカバレッジがどれだけ改善するか」を数値化する。

    カバレッジは3つのレイヤーで評価:
      1. 大分類ロール (Fighter/Tank/Support/Mage/Assassin/ADC)
         → チームに1体もいないロールを埋める候補を最優先
      2. 戦術タグ (CC, Sustain, Enguage, etc.)
         → チームに無い戦術能力を持つ候補に加点
      3. パワースパイクバランス
         → チームの時間帯（序盤/中盤/終盤）が偏りすぎない構成を優遇

    どのタグがカバレッジ対象かは coverage_config で外部から指定する。
    将来的にはCSV側のメタ情報から自動生成する想定。
"""

from __future__ import annotations
import pandas as pd
import numpy as np


# ---------------------------------------------------------------------------
# デフォルト設定（CSV側のメタ情報が来るまでの仮設定）
# ---------------------------------------------------------------------------
DEFAULT_COVERAGE_CONFIG = {
    # レイヤー1: 大分類ロール（必須カバレッジ）
    # チームに0体のロールがあれば、そのロールを持つ候補に大きく加点
    "role_tags": ["Fighter", "Tank", "Support", "Mage", "Assassin", "ADC"],
    "role_weight": 3.0,

    # レイヤー2: 戦術タグ（ボーナスカバレッジ）
    # チームに無い戦術能力を持つ候補に小さく加点
    "tactic_tags": [
        "CC", "Sustain", "Enguage", "DisEnguage",
        "AOE", "Touch", "LastHit", "Blink",
    ],
    "tactic_weight": 1.0,

    # レイヤー3: パワースパイクバランス
    "powerspike_weight": 1.5,
    # パワースパイクを3区分に分ける閾値
    "ps_early_max": 4,    # 3-4 = 序盤型
    "ps_mid_max": 6,      # 5-6 = 中盤型
    # 7-9 = 終盤型
}


def _get_team_tags(team: list[str], df: pd.DataFrame, tag_cols: list[str]) -> set[str]:
    """
    現在のチームメンバーがカバーしているタグの集合を返す。

    あるタグが1つでもチーム内のポケモンに1が立っていれば「カバー済み」。
    """
    if not team:
        return set()
    team_rows = df[df["name"].isin(team)]
    covered = set()
    for col in tag_cols:
        if col in team_rows.columns and team_rows[col].sum() > 0:
            covered.add(col)
    return covered


def _candidate_new_tags(
    candidate: str,
    covered: set[str],
    df: pd.DataFrame,
    tag_cols: list[str],
) -> list[str]:
    """
    候補ポケモンが持っていて、かつチームにまだ無いタグのリストを返す。
    """
    row = df[df["name"] == candidate].iloc[0]
    new_tags = []
    for col in tag_cols:
        if col not in covered and col in row.index and row[col] == 1:
            new_tags.append(col)
    return new_tags


def _powerspike_balance_score(
    candidate: str,
    current_team: list[str],
    df: pd.DataFrame,
    config: dict,
) -> float:
    """
    候補を追加したときの、パワースパイク分布のバランス改善度を返す。

    3区分（序盤/中盤/終盤）にそれぞれ何体いるかを見て、
    空いている区分を埋める候補に加点する。

    Returns
    -------
    float
        0.0（改善なし）～ 1.0（空いてた区分を埋めた）
    """
    early_max = config.get("ps_early_max", 4)
    mid_max = config.get("ps_mid_max", 6)

    def classify(ps: int) -> str:
        if ps <= early_max:
            return "early"
        elif ps <= mid_max:
            return "mid"
        else:
            return "late"

    # 現在のチームのPS分布
    team_ps = df[df["name"].isin(current_team)]["PowerSpike"].tolist()
    team_bins = {"early": 0, "mid": 0, "late": 0}
    for ps in team_ps:
        if pd.notna(ps):
            team_bins[classify(int(ps))] += 1

    # 候補のPS区分
    cand_row = df[df["name"] == candidate].iloc[0]
    cand_ps = cand_row["PowerSpike"]
    if pd.isna(cand_ps):
        return 0.0
    cand_bin = classify(int(cand_ps))

    # 空いている区分を埋めるなら高評価
    empty_bins = [b for b, c in team_bins.items() if c == 0]
    if cand_bin in empty_bins:
        return 1.0

    # 最も人数が少ない区分を埋めるなら中評価
    min_count = min(team_bins.values())
    if team_bins[cand_bin] == min_count:
        return 0.3

    # 既に多い区分にさらに追加 → 加点なし
    return 0.0


def coverage_score(
    candidate: str,
    current_team: list[str],
    df: pd.DataFrame,
    config: dict | None = None,
) -> float:
    """
    候補ポケモンを current_team に追加したときの、カバレッジ改善スコアを返す。

    Parameters
    ----------
    candidate : str
        候補ポケモン名（roles_clean.csv の name 列と同一表記）。
    current_team : list[str]
        現在の自チームメンバー（0～4体）。
    df : pd.DataFrame
        roles_clean.csv を読み込んだ DataFrame。
    config : dict, optional
        カバレッジ判定の設定。省略時は DEFAULT_COVERAGE_CONFIG を使用。

    Returns
    -------
    float
        カバレッジ改善スコア（高いほど良い）。
    """
    if config is None:
        config = DEFAULT_COVERAGE_CONFIG

    score = 0.0

    # --- レイヤー1: 大分類ロール ---
    role_tags = config["role_tags"]
    role_weight = config["role_weight"]
    role_covered = _get_team_tags(current_team, df, role_tags)
    role_new = _candidate_new_tags(candidate, role_covered, df, role_tags)
    score += len(role_new) * role_weight

    # --- レイヤー2: 戦術タグ ---
    tactic_tags = config["tactic_tags"]
    tactic_weight = config["tactic_weight"]
    tactic_covered = _get_team_tags(current_team, df, tactic_tags)
    tactic_new = _candidate_new_tags(candidate, tactic_covered, df, tactic_tags)
    score += len(tactic_new) * tactic_weight

    # --- レイヤー3: パワースパイクバランス ---
    ps_weight = config["powerspike_weight"]
    ps_score = _powerspike_balance_score(candidate, current_team, df, config)
    score += ps_score * ps_weight

    return score


def coverage_report(team: list[str], df: pd.DataFrame, config: dict | None = None) -> dict:
    """
    完成したチーム（5体）のカバレッジ状況をレポートする。

    Returns
    -------
    dict
        {
            "role_covered": [...],
            "role_missing": [...],
            "tactic_covered": [...],
            "tactic_missing": [...],
            "powerspike_distribution": {"early": n, "mid": n, "late": n},
        }
    """
    if config is None:
        config = DEFAULT_COVERAGE_CONFIG

    role_covered = _get_team_tags(team, df, config["role_tags"])
    role_missing = set(config["role_tags"]) - role_covered

    tactic_covered = _get_team_tags(team, df, config["tactic_tags"])
    tactic_missing = set(config["tactic_tags"]) - tactic_covered

    early_max = config.get("ps_early_max", 4)
    mid_max = config.get("ps_mid_max", 6)
    team_ps = df[df["name"].isin(team)]["PowerSpike"].tolist()
    ps_dist = {"early": 0, "mid": 0, "late": 0}
    for ps in team_ps:
        if pd.notna(ps):
            v = int(ps)
            if v <= early_max:
                ps_dist["early"] += 1
            elif v <= mid_max:
                ps_dist["mid"] += 1
            else:
                ps_dist["late"] += 1

    return {
        "role_covered": sorted(role_covered),
        "role_missing": sorted(role_missing),
        "tactic_covered": sorted(tactic_covered),
        "tactic_missing": sorted(tactic_missing),
        "powerspike_distribution": ps_dist,
    }


# ---------------------------------------------------------------------------
# テスト
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    df = pd.read_csv("roles_clean.csv")

    print("=" * 60)
    print("テスト: チーム構築シミュレーション")
    print("=" * 60)

    # 空チームから1体ずつ追加して、カバレッジスコアの変化を見る
    team = []
    candidates_sequence = [
        "リザードン",           # Fighter (PS=9, 終盤型)
        "ピクシー",             # Support/HealSupport (PS=4, 序盤型)
        "カメックス",           # Tank (PS=7, 終盤型)
        "ピカチュウ (ボルテ)",   # Mage/CC (PS=4, 序盤型)
        "ゲンガー",             # Assassin (PS=5, 中盤型)
    ]

    for cand in candidates_sequence:
        sc = coverage_score(cand, team, df)
        print(f"\n--- {cand} を追加 (現在{len(team)}体) → coverage_score = {sc:.1f} ---")

        # 比較: 他の候補のスコアも数体見る
        others = [n for n in df["name"].tolist() if n != cand and n not in team][:5]
        for o in others:
            osc = coverage_score(o, team, df)
            print(f"  (比較) {o}: {osc:.1f}")

        team.append(cand)

    print("\n" + "=" * 60)
    print("最終チームのカバレッジレポート:")
    print("=" * 60)
    report = coverage_report(team, df)
    print(f"  ロール カバー済み: {report['role_covered']}")
    print(f"  ロール 不足:       {report['role_missing']}")
    print(f"  戦術タグ カバー済み: {report['tactic_covered']}")
    print(f"  戦術タグ 不足:       {report['tactic_missing']}")
    print(f"  パワースパイク分布: {report['powerspike_distribution']}")