import os

os.environ.setdefault("GUILD_IDS", "0")  # panel の import 時に GUILD_IDS を参照するため

from src.modules.panel import build_number_values, build_pick_counts


def test_build_number_values_team_starts_at_two():
    # チーム数は 1 チームを許さず 2 から始まる
    assert build_number_values("team", 4) == [2, 3, 4]


def test_build_number_values_size_starts_at_one():
    assert build_number_values("size", 4) == [1, 2, 3, 4]


def test_build_number_values_capped_at_max_buttons():
    # メンバーが多くてもボタンは 5 個まで
    assert build_number_values("size", 20) == [1, 2, 3, 4, 5]
    assert build_number_values("team", 20) == [2, 3, 4, 5, 6]


def test_build_number_values_too_few_members_returns_empty():
    # 1 人しかいなければチーム分割の候補は出ない
    assert build_number_values("team", 1) == []


def test_build_pick_counts_is_one_to_five():
    # ランダムピックの抽選数は 1〜5 の固定値
    assert build_pick_counts() == [1, 2, 3, 4, 5]
