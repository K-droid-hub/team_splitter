from types import SimpleNamespace

from src.grouping import SplitTeam, format_member_entry
from src.user_aspiration_store import UserAspirationStore


def make_member(display_name, user_id, bot=False):
    return SimpleNamespace(id=user_id, display_name=display_name, bot=bot)


def make_ctx(members, guild_id=100, channel_id=10):
    # set_member は discord.Interaction 以外なら author.voice を参照する
    channel = SimpleNamespace(id=channel_id, members=members)
    voice = SimpleNamespace(channel=channel)
    return SimpleNamespace(
        author=SimpleNamespace(voice=voice), guild=SimpleNamespace(id=guild_id)
    )


def make_split_team():
    return SplitTeam(aspiration_store=UserAspirationStore(db_path=":memory:"))


def test_format_member_entry_with_and_without_aspiration():
    assert format_member_entry("アリス", "勝つぞ") == "アリス\n*勝つぞ*"
    # 意気込みが無ければ名前だけ(行を詰める)
    assert format_member_entry("ボブ", "") == "ボブ"


def test_set_member_annotates_aspiration():
    team = make_split_team()
    team.set_aspiration(100, 1, "アリス", "がんばる")
    members = [
        make_member("アリス", 1), make_member("ボブ", 2), make_member("bot", 3, bot=True)
    ]
    team.set_member(make_ctx(members, guild_id=100))
    aspiration_by_name = {p.name: p.aspiration for p in team.channel_members}
    # bot は除外され、意気込み未設定のボブは空文字になる
    assert aspiration_by_name == {"アリス": "がんばる", "ボブ": ""}


def test_split_output_includes_aspiration_line():
    team = make_split_team()
    team.set_aspiration(100, 1, "アリス", "勝つぞ")
    members = [make_member("アリス", 1), make_member("ボブ", 2)]
    result = team.split(make_ctx(members, guild_id=100), teamCount=1, teamSize=0)
    # 意気込みのあるアリスは 2 行目に斜体で付く
    assert "アリス\n*勝つぞ*" in result
    # 意気込みの無いボブは名前だけで斜体行が付かない
    assert "ボブ" in result
    assert "ボブ\n*" not in result


def test_aspiration_not_shared_across_guilds():
    # サーバー A で設定した意気込みは、同名ユーザーでもサーバー B には出ない
    team = make_split_team()
    team.set_aspiration(100, 1, "アリス", "サーバーAだけ")
    members = [make_member("アリス", 1)]
    team.set_member(make_ctx(members, guild_id=200))
    assert team.channel_members[0].aspiration == ""


def test_aspiration_follows_user_after_rename():
    # ニックネームを変えても、user_id で引き当てるので意気込みは残る
    team = make_split_team()
    team.set_aspiration(100, 1, "アリス", "がんばる")
    team.set_member(make_ctx([make_member("アリス改名", 1)], guild_id=100))
    assert team.channel_members[0].aspiration == "がんばる"


def test_aspiration_not_shared_between_same_display_names():
    # 表示名が同じ 2 人でも、意気込みは user_id ごとに別で持つ
    team = make_split_team()
    team.set_aspiration(100, 1, "たろう", "1 人目だけ")
    team.set_member(make_ctx([make_member("たろう", 1), make_member("たろう", 2)]))
    aspirations = {p.user_id: p.aspiration for p in team.channel_members}
    assert aspirations == {1: "1 人目だけ", 2: ""}


def test_set_aspiration_reflects_to_held_members():
    # 設定した意気込みは、保持中のメンバーにも即反映される
    team = make_split_team()
    team.set_member(make_ctx([make_member("アリス", 1)]))
    team.set_aspiration(100, 1, "アリス", "あとから設定")
    assert team.channel_members[0].aspiration == "あとから設定"
