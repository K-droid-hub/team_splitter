from types import SimpleNamespace

from src.grouping import SplitTeam
from src.user_aspiration_store import UserAspirationStore


def make_member(display_name, user_id, bot=False):
    return SimpleNamespace(id=user_id, display_name=display_name, bot=bot)


def make_ctx(members, guild_id=100, channel_id=10):
    channel = SimpleNamespace(id=channel_id, members=members)
    voice = SimpleNamespace(channel=channel)
    return SimpleNamespace(
        author=SimpleNamespace(voice=voice), guild=SimpleNamespace(id=guild_id)
    )


def make_split_team():
    return SplitTeam(aspiration_store=UserAspirationStore(db_path=":memory:"))


def register_viewer(team, ctx, name, user_id=None):
    return team.add_special(
        ctx, addName=name, isViewer=True, isPriority=False, isRocked=True, user_id=user_id
    )


def viewer_names(team, ctx):
    team.set_member(ctx)
    return [p.name for p in team.channel_members if p.viewer]


def test_registration_survives_other_guild_activity():
    # サーバー A の登録が、サーバー B の操作(set_member)で消えないこと
    team = make_split_team()
    ctx_a = make_ctx([make_member("アリス", 1), make_member("ボブ", 2)], guild_id=100)
    register_viewer(team, ctx_a, "アリス")

    team.set_member(make_ctx([make_member("キャロル", 3)], guild_id=200, channel_id=20))

    assert viewer_names(team, ctx_a) == ["アリス"]


def test_registration_survives_other_channel_in_same_guild():
    # 同じサーバーでも VC が違えば互いの登録を壊さないこと
    team = make_split_team()
    ctx_vc1 = make_ctx([make_member("アリス", 1)], guild_id=100, channel_id=10)
    ctx_vc2 = make_ctx([make_member("ボブ", 2)], guild_id=100, channel_id=20)
    register_viewer(team, ctx_vc1, "アリス")
    register_viewer(team, ctx_vc2, "ボブ")

    assert viewer_names(team, ctx_vc1) == ["アリス"]
    assert viewer_names(team, ctx_vc2) == ["ボブ"]


def test_two_guilds_two_channels_each_are_independent():
    # 2 サーバー × 各 2 チャンネルを同時に抱えても取り違えないこと
    team = make_split_team()
    contexts = {}
    for guild_id in (100, 200):
        for channel_id in (10, 20):
            name = f"g{guild_id}c{channel_id}"
            user_id = guild_id + channel_id
            ctx = make_ctx([make_member(name, user_id)], guild_id, channel_id)
            contexts[name] = ctx
            register_viewer(team, ctx, name)
    for name, ctx in contexts.items():
        assert viewer_names(team, ctx) == [name]


def test_registration_survives_member_rename():
    # 識別は user_id なので、改名しても観戦者登録が外れないこと
    team = make_split_team()
    ctx = make_ctx([make_member("アリス", 1)], guild_id=100)
    register_viewer(team, ctx, "アリス")

    renamed = make_ctx([make_member("アリス改名", 1)], guild_id=100)
    assert viewer_names(team, renamed) == ["アリス改名"]


def test_same_display_name_members_are_targeted_by_user_id():
    # 表示名が同じ 2 人でも、user_id 指定なら取り違えないこと
    team = make_split_team()
    ctx = make_ctx([make_member("たろう", 1), make_member("たろう", 2)], guild_id=100)
    register_viewer(team, ctx, "たろう", user_id=2)

    team.set_member(ctx)
    assert [p.user_id for p in team.channel_members if p.viewer] == [2]


def test_delete_special_rereads_current_voice_channel():
    # 解除も今の VC を読み直すので、別 VC の操作を挟んでも正しく外れること
    team = make_split_team()
    ctx = make_ctx([make_member("アリス", 1)], guild_id=100, channel_id=10)
    register_viewer(team, ctx, "アリス")
    team.set_member(make_ctx([make_member("ボブ", 2)], guild_id=200, channel_id=20))

    team.delete_special(ctx, "アリス", isViewer=True, isPriority=False, user_id=1)
    assert viewer_names(team, ctx) == []


def test_delete_special_requires_voice_channel():
    team = make_split_team()
    ctx = make_ctx([make_member("アリス", 1)])
    ctx.author.voice = None
    assert team.delete_special(ctx, "アリス", isViewer=True, isPriority=False) == team.vc_err


def test_reset_all_is_scoped_to_guild():
    # /リセット は実行したサーバーだけを解除し、他サーバーには波及しないこと
    team = make_split_team()
    ctx_a = make_ctx([make_member("アリス", 1)], guild_id=100, channel_id=10)
    ctx_b = make_ctx([make_member("ボブ", 2)], guild_id=200, channel_id=20)
    register_viewer(team, ctx_a, "アリス")
    register_viewer(team, ctx_b, "ボブ")

    team.reset_all(ctx_a)

    assert viewer_names(team, ctx_a) == []
    assert viewer_names(team, ctx_b) == ["ボブ"]


def test_set_member_drops_members_who_left():
    team = make_split_team()
    ctx = make_ctx([make_member("アリス", 1), make_member("ボブ", 2)], guild_id=100)
    team.set_member(ctx)
    left = make_ctx([make_member("アリス", 1)], guild_id=100)
    team.set_member(left)
    assert [p.user_id for p in team.channel_members] == [1]
    assert team.memberSize == 1
