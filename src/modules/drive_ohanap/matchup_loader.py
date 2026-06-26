"""
matchup_loader.py
------------------
対面相性表（1on1 matchup matrix）の読み込み・補完・検証。

入力フォーマット:
    - N×N の正方CSV。1列目・1行目にポケモン名（roles_clean.csv の name 列と同一表記）。
    - 左下三角（i行目 > j列目、つまり対角線より下）だけ値が入っている。
    - 値は -1（i視点で不利）, 0（互角）, 1（i視点で有利）。
    - 右上三角・空白セルは未入力（NaN）として扱う。

出力:
    - 完全に対称補完済みの N×N DataFrame（欠損は 0 = 互角でフォールバック）。
    - 検証レポート（対角線が0でない、対称性が壊れている、値が範囲外、などを検出）。
"""

from __future__ import annotations
import pandas as pd
import numpy as np


VALID_VALUES = {-1.0, 0.0, 1.0}


def load_matchup_csv(path: str, expected_names: list[str] | None = None) -> pd.DataFrame:
    """
    左下三角だけ埋まった matchup CSV を読み込み、右上三角を符号反転で補完して返す。

    Parameters
    ----------
    path : str
        matchup CSV のパス。1列目がindex、1行目がheaderとしてポケモン名が入っている前提。
    expected_names : list[str], optional
        roles_clean.csv 等から取得した正式な名前リスト。指定すると名前の不一致を検出する。

    Returns
    -------
    pd.DataFrame
        対称補完・欠損0埋め済みの正方DataFrame。
    """
    df = pd.read_csv(path, index_col=0)

    # --- 名前の整合性チェック ---
    if expected_names is not None:
        expected_set = set(expected_names)
        actual_set = set(df.index) | set(df.columns)
        missing_in_matchup = expected_set - actual_set
        extra_in_matchup = actual_set - expected_set
        if missing_in_matchup:
            print(f"[警告] roles側にあるがmatchup側に無い名前: {sorted(missing_in_matchup)}")
        if extra_in_matchup:
            print(f"[警告] matchup側にあるがroles側に無い名前: {sorted(extra_in_matchup)}")

    if list(df.index) != list(df.columns):
        raise ValueError("行と列の名前リストが一致していません。順序・表記を確認してください。")

    n = len(df)
    values = df.to_numpy(dtype=float)

    # --- 値の範囲チェック（NaN以外で -1/0/1 以外があれば警告） ---
    finite_mask = ~np.isnan(values)
    bad_mask = finite_mask & ~np.isin(values, list(VALID_VALUES))
    if bad_mask.any():
        bad_positions = np.argwhere(bad_mask)
        examples = [
            (df.index[i], df.columns[j], values[i, j]) for i, j in bad_positions[:10]
        ]
        raise ValueError(f"-1/0/1 以外の値が見つかりました（最大10件表示）: {examples}")

    # --- 上三角・下三角の整合性チェック（両方入力されている場合のみ） ---
    conflicts = []
    for i in range(n):
        for j in range(i + 1, n):  # j > i は本来「未入力(上三角)」のはずだが、誤って入力されているケースを検出
            upper = values[i, j]
            lower = values[j, i]
            if not np.isnan(upper) and not np.isnan(lower):
                if upper != -lower:
                    conflicts.append((df.index[i], df.columns[j], upper, lower))
    if conflicts:
        print(f"[警告] 上三角と下三角の両方に矛盾する値が入力されています（最大10件表示）:")
        for name_i, name_j, u, l in conflicts[:10]:
            print(f"   {name_i} vs {name_j}: 上三角={u}, 下三角={l}（符号反転していません）")

    # --- 対称補完 ---
    # 下三角(i>j)の値を優先的に正とし、上三角は -1倍で埋める。
    # 既に上三角に値が入っていてかつ矛盾がない場合はそのまま使う。
    completed = values.copy()
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            if np.isnan(completed[i, j]) and not np.isnan(completed[j, i]):
                completed[i, j] = -completed[j, i]

    # --- 対角線は常に0 ---
    np.fill_diagonal(completed, 0.0)

    # --- 残った欠損（両方未入力）は 0 = 互角 でフォールバック ---
    remaining_nan = np.isnan(completed)
    n_missing = remaining_nan.sum()
    if n_missing > 0:
        coverage = 1 - n_missing / (n * n - n)  # 対角線を除いた被覆率
        print(f"[情報] 両方向とも未入力のセルが {n_missing} 個あります（対角線除いた被覆率: {coverage:.1%}）。0(互角)で補完します。")
    completed[remaining_nan] = 0.0

    return pd.DataFrame(completed, index=df.index, columns=df.columns)


def get_matchup_score(matchup: pd.DataFrame, my_pokemon: str, enemy_pokemon: str) -> float:
    """my_pokemon視点でenemy_pokemonとの対面スコアを返す（1=有利, -1=不利, 0=互角）。"""
    return matchup.loc[my_pokemon, enemy_pokemon]


def team_matchup_score(matchup: pd.DataFrame, my_team: list[str], enemy_team: list[str]) -> float:
    """自チーム5体それぞれの、敵チーム5体に対する対面スコアの合計。"""
    return sum(
        get_matchup_score(matchup, mine, enemy)
        for mine in my_team
        for enemy in enemy_team
    )


if __name__ == "__main__":
    roles = pd.read_csv("roles_clean.csv")
    names = roles["name"].tolist()

    matchup = load_matchup_csv("matchup_mock_lower.csv", expected_names=names)

    # 対称性の最終確認（completed後は厳密に反対称であるはず）
    arr = matchup.to_numpy()
    assert np.allclose(arr, -arr.T), "補完後も対称性が崩れています"
    assert np.allclose(np.diag(arr), 0), "対角線が0になっていません"
    print("\n[OK] 対称性・対角線チェック通過")

    print("\n補完後のサンプル（先頭5x5）:")
    print(matchup.iloc[:5, :5])

    # 動作確認: 適当な2体のスコアを引いてみる
    a, b = names[0], names[1]
    print(f"\n{a} の {b} に対する対面スコア: {get_matchup_score(matchup, a, b)}")
    print(f"{b} の {a} に対する対面スコア: {get_matchup_score(matchup, b, a)}")