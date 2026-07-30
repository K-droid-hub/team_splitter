from types import SimpleNamespace

from src.grouping import SplitTeam, format_member_entry
from src.user_aspiration_store import UserAspirationStore


def make_member(display_name, bot=False):
    return SimpleNamespace(display_name=display_name, bot=bot)


def make_ctx(members):
    # set_member は discord.Interaction 以外なら author.voice を参照する
    channel = SimpleNamespace(members=members)
    voice = SimpleNamespace(channel=channel)
    return SimpleNamespace(author=SimpleNamespace(voice=voice))


def make_split_team():
    return SplitTeam(aspiration_store=UserAspirationStore(db_path=":memory:"))


def test_format_member_entry_with_and_without_aspiration():
    assert format_member_entry("アリス", "勝つぞ") == "アリス\n*勝つぞ*"
    # 意気込みが無ければ名前だけ(行を詰める)
    assert format_member_entry("ボブ", "") == "ボブ"


def test_set_member_annotates_aspiration():
    team = make_split_team()
    team.set_aspiration(1, "アリス", "がんばる")
    members = [make_member("アリス"), make_member("ボブ"), make_member("bot", bot=True)]
    team.set_member(make_ctx(members))
    aspiration_by_name = {p.name: p.aspiration for p in team.channel_members}
    # bot は除外され、意気込み未設定のボブは空文字になる
    assert aspiration_by_name == {"アリス": "がんばる", "ボブ": ""}


def test_split_output_includes_aspiration_line():
    team = make_split_team()
    team.set_aspiration(1, "アリス", "勝つぞ")
    members = [make_member("アリス"), make_member("ボブ")]
    result = team.split(make_ctx(members), teamCount=1, teamSize=0)
    # 意気込みのあるアリスは 2 行目に斜体で付く
    assert "アリス\n*勝つぞ*" in result
    # 意気込みの無いボブは名前だけで斜体行が付かない
    assert "ボブ" in result
    assert "ボブ\n*" not in result
