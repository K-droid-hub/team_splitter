import asyncio

import pytest

from src.voice_pool import VoicePool, VoiceSlotUnavailable
from src.voice_session import USE_MUSIC, USE_TTS


class FakeGuild:
    def __init__(self, guild_id):
        self.id = guild_id
        self.voice_client = None


class FakeVoiceClient:
    def __init__(self, channel, owner):
        self.channel = channel
        self.guild = channel.guild
        self.owner = owner
        self.connected = True

    def is_connected(self):
        return self.connected

    def is_playing(self):
        return False

    async def disconnect(self):
        self.connected = False
        self.guild.voice_client = None


class FakeChannel:
    def __init__(self, channel_id, guild, owner, delay=0):
        self.id = channel_id
        self.name = f"vc{channel_id}"
        self.guild = guild
        self.owner = owner
        # 接続中に別の要求を割り込ませたいテストのための待ち時間
        self.delay = delay

    async def connect(self):
        if self.delay:
            await asyncio.sleep(self.delay)
        voice_client = FakeVoiceClient(self, self.owner)
        self.guild.voice_client = voice_client
        return voice_client


class FakeClient:
    """get_guild / get_channel / is_ready だけを持つ最小のクライアント。

    bot ごとにキャッシュも voice_client も独立しているので、FakeClient も
    自分専用の FakeGuild / FakeChannel を持つ
    """

    def __init__(self, name, channels, delay=0, ready=True):
        # channels は {channel_id: guild_id}
        self.name = name
        self.ready = ready
        self.guilds = {}
        self.channels = {}
        for channel_id, guild_id in channels.items():
            guild = self.guilds.setdefault(guild_id, FakeGuild(guild_id))
            self.channels[channel_id] = FakeChannel(channel_id, guild, self, delay)

    def is_ready(self):
        return self.ready

    def get_guild(self, guild_id):
        return self.guilds.get(guild_id)

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)


ALL_CHANNELS = {10: 1, 11: 1, 12: 1, 20: 2}


def make_pool(bot1_channels=ALL_CHANNELS, bot2_channels=ALL_CHANNELS, delay=0, bot2_ready=True):
    bot1 = FakeClient("bot1", bot1_channels, delay)
    bot2 = FakeClient("bot2", bot2_channels, delay, ready=bot2_ready)
    return VoicePool([bot1, bot2]), bot1, bot2


def owner_of(session):
    return session.voice_client.owner.name


# ---- 割り当ての優先順位 ----

def test_first_request_uses_the_main_bot():
    pool, bot1, _ = make_pool()
    session = asyncio.run(pool.acquire(bot1.get_channel(10), USE_TTS))
    assert owner_of(session) == "bot1"


def test_same_channel_shares_one_bot():
    # 読み上げ中のチャンネルで音楽を頼んでも、2 体目は入ってこない
    pool, bot1, bot2 = make_pool()

    async def scenario():
        tts = await pool.acquire(bot1.get_channel(10), USE_TTS)
        music = await pool.acquire(bot1.get_channel(10), USE_MUSIC)
        return tts, music

    tts, music = asyncio.run(scenario())
    assert tts is music
    assert tts.users == {USE_TTS, USE_MUSIC}
    assert bot2.get_guild(1).voice_client is None


def test_another_channel_uses_the_second_bot():
    pool, bot1, _ = make_pool()

    async def scenario():
        await pool.acquire(bot1.get_channel(10), USE_TTS)
        return await pool.acquire(bot1.get_channel(11), USE_MUSIC)

    assert owner_of(asyncio.run(scenario())) == "bot2"


def test_third_channel_is_refused_with_where_it_is_used():
    pool, bot1, _ = make_pool()

    async def scenario():
        await pool.acquire(bot1.get_channel(10), USE_TTS)
        await pool.acquire(bot1.get_channel(11), USE_MUSIC)
        await pool.acquire(bot1.get_channel(12), USE_MUSIC)

    with pytest.raises(VoiceSlotUnavailable) as error:
        asyncio.run(scenario())
    message = str(error.value)
    assert "2 つまで" in message
    # どこで使っているかを添え、そちらへ移ればいいと分かるようにする
    assert "🔊vc10" in message and "🔊vc11" in message


def test_other_server_is_not_affected():
    # 接続の上限はサーバー単位なので、別サーバーは埋まった影響を受けない
    pool, bot1, _ = make_pool()

    async def scenario():
        await pool.acquire(bot1.get_channel(10), USE_TTS)
        await pool.acquire(bot1.get_channel(11), USE_MUSIC)
        return await pool.acquire(bot1.get_channel(20), USE_MUSIC)

    assert owner_of(asyncio.run(scenario())) == "bot1"


# ---- 1 体だけ招待されているサーバー ----

def test_main_bot_only_server_allows_one_channel():
    pool, bot1, _ = make_pool(bot2_channels={20: 2})
    assert pool.capacity(1) == 1

    async def scenario():
        await pool.acquire(bot1.get_channel(10), USE_TTS)
        await pool.acquire(bot1.get_channel(11), USE_MUSIC)

    with pytest.raises(VoiceSlotUnavailable) as error:
        asyncio.run(scenario())
    # 招待を促すのではなく、このサーバーの上限として伝える
    assert "1 つまで" in str(error.value)
    assert "招待" not in str(error.value)


def test_main_bot_only_server_still_mixes_in_one_channel():
    pool, bot1, _ = make_pool(bot2_channels={20: 2})

    async def scenario():
        tts = await pool.acquire(bot1.get_channel(10), USE_TTS)
        music = await pool.acquire(bot1.get_channel(10), USE_MUSIC)
        return tts, music

    tts, music = asyncio.run(scenario())
    assert tts is music


def test_skips_second_bot_that_is_still_starting():
    pool, bot1, bot2 = make_pool(bot2_ready=False)

    async def scenario():
        await pool.acquire(bot1.get_channel(10), USE_TTS)
        await pool.acquire(bot1.get_channel(11), USE_MUSIC)

    with pytest.raises(VoiceSlotUnavailable):
        asyncio.run(scenario())
    assert bot2.get_guild(1).voice_client is None


# ---- 同時に要求が来たとき ----

def test_concurrent_requests_in_different_channels_get_different_bots():
    pool, bot1, _ = make_pool(delay=0.01)

    async def scenario():
        return await asyncio.gather(
            pool.acquire(bot1.get_channel(10), USE_TTS),
            pool.acquire(bot1.get_channel(11), USE_MUSIC),
        )

    first, second = asyncio.run(scenario())
    assert {owner_of(first), owner_of(second)} == {"bot1", "bot2"}


def test_concurrent_requests_in_the_same_channel_share_one_bot():
    # 同じチャンネルへの同時要求で、2 体とも入ってきて並ばないこと
    pool, bot1, bot2 = make_pool(delay=0.01)

    async def scenario():
        return await asyncio.gather(
            pool.acquire(bot1.get_channel(10), USE_TTS),
            pool.acquire(bot1.get_channel(10), USE_MUSIC),
        )

    first, second = asyncio.run(scenario())
    assert first is second
    assert bot2.get_guild(1).voice_client is None


# ---- 後片付け ----

def test_released_slot_can_be_used_again():
    pool, bot1, _ = make_pool()

    async def scenario():
        session = await pool.acquire(bot1.get_channel(10), USE_TTS)
        await session.release(USE_TTS)
        return await pool.acquire(bot1.get_channel(11), USE_MUSIC)

    assert owner_of(asyncio.run(scenario())) == "bot1"
    assert pool.sessions_in_guild(1)[0].channel_id == 11


def test_externally_disconnected_session_is_not_reused():
    # 外から切断された接続へ相乗りすると、音が出ないまま待ち続けてしまう
    pool, bot1, _ = make_pool()

    async def scenario():
        old = await pool.acquire(bot1.get_channel(10), USE_TTS)
        await old.voice_client.disconnect()
        new = await pool.acquire(bot1.get_channel(10), USE_MUSIC)
        return old, new

    old, new = asyncio.run(scenario())
    assert new is not old
    assert new.is_alive() is True
