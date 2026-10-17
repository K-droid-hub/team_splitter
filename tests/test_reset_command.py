import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("GUILD_IDS", "0")

from src.grouping import SplitTeam
from src.modules.reset_all import ResetAll
from src.user_aspiration_store import UserAspirationStore


def make_member(display_name, user_id):
    return SimpleNamespace(id=user_id, display_name=display_name, bot=False)


def make_ctx(members, guild_id):
    voice = SimpleNamespace(channel=SimpleNamespace(id=10, members=members))
    return SimpleNamespace(
        author=SimpleNamespace(voice=voice),
        guild=SimpleNamespace(id=guild_id),
        send=AsyncMock(),
    )


def test_reset_command_clears_only_the_invoking_guild():
    # !reset は実行したサーバーの観戦者・優先者だけを解除し、他サーバーには波及させない
    async def scenario():
        team = SplitTeam(aspiration_store=UserAspirationStore(db_path=":memory:"))
        ctx_a = make_ctx([make_member("A", 1)], guild_id=100)
        ctx_b = make_ctx([make_member("B", 2)], guild_id=200)
        for ctx, name in [(ctx_a, "A"), (ctx_b, "B")]:
            team.add_special(ctx, addName=name, isViewer=True, isPriority=False, isRocked=True)

        cog = ResetAll(SimpleNamespace(split_team=team))
        await ResetAll.reset_command.callback(cog, ctx_a)

        team.set_member(ctx_a)
        assert [m for m in team.channel_members if m.viewer] == []
        team.set_member(ctx_b)
        assert [m.name for m in team.channel_members if m.viewer] == ["B"]
        ctx_a.send.assert_awaited_once()

    asyncio.run(scenario())
