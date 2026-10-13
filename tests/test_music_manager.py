import asyncio
from unittest.mock import patch

from src.music_manager import MAX_QUEUE_SIZE, MusicManager
from src.music_source import Track
from src.voice_session import USE_MUSIC


def track(title):
    return Track(title=title, webpage_url=f"https://example.com/{title}", duration=90)


class FakeSource:
    def __init__(self, failing_titles=()):
        self.failing_titles = set(failing_titles)
        self.resolved = []

    async def resolve_stream_url(self, track):
        if track.title in self.failing_titles:
            raise RuntimeError("解決に失敗")
        self.resolved.append(track.title)
        return f"https://stream.example.com/{track.title}"


class FakeSession:
    """VoiceSession の代わり。曲はすぐ終わるか、skip されるまで続く"""

    def __init__(self, channel_id=10, guild_id=1, auto_finish=True):
        self.channel_id = channel_id
        self.guild_id = guild_id
        self.auto_finish = auto_finish
        self.alive = True
        self.played = []
        self.released = []
        self.skips = 0
        self._playing = None

    def is_alive(self):
        return self.alive

    async def play_track(self, source):
        self.played.append(source)
        if self.auto_finish:
            return
        self._playing = asyncio.Event()
        await self._playing.wait()

    def skip_track(self):
        self.skips += 1
        if self._playing is not None:
            self._playing.set()

    async def release(self, user):
        self.released.append(user)


class FakeTextChannel:
    def __init__(self):
        self.sent = []

    async def send(self, content):
        self.sent.append(content)


class FakeAudio:
    def __init__(self, url, before_options=None, options=None):
        self.url = url


async def wait_for(predicate, timeout=1.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0)
    return False


def pcm_audio():
    return patch("src.music_manager.discord.FFmpegPCMAudio", FakeAudio)


# ---- キュー操作 ----

def test_enqueue_returns_accepted_count():
    async def scenario():
        manager = MusicManager(source=FakeSource())
        state = manager.start(FakeSession(auto_finish=False), FakeTextChannel())
        with pcm_audio():
            accepted = manager.enqueue(state, [track("a"), track("b")])
            await manager.stop(state)
        return accepted

    assert asyncio.run(scenario()) == 2


def test_enqueue_stops_at_the_queue_limit():
    # 上限を超えた分は捨て、実際に積めた数だけを返す
    async def scenario():
        manager = MusicManager(source=FakeSource())
        state = manager.start(FakeSession(auto_finish=False), FakeTextChannel())
        with pcm_audio():
            accepted = manager.enqueue(
                state, [track(str(i)) for i in range(MAX_QUEUE_SIZE + 5)]
            )
            # ワーカーが 1 曲取り出して再生中になるのを待ち、空いた 1 枠だけ積めることを見る
            await wait_for(lambda: state.current is not None)
            more = manager.enqueue(state, [track("x"), track("y")])
            await manager.stop(state)
        return accepted, more

    accepted, more = asyncio.run(scenario())
    assert accepted == MAX_QUEUE_SIZE
    assert more == 1


def test_finds_state_by_voice_channel_and_server():
    async def scenario():
        manager = MusicManager(source=FakeSource())
        first = manager.start(FakeSession(channel_id=10, guild_id=1), None)
        second = manager.start(FakeSession(channel_id=11, guild_id=1), None)
        other = manager.start(FakeSession(channel_id=20, guild_id=2), None)
        found = (
            manager.state_for_voice(11),
            manager.state_for_voice(99),
            manager.states_in_guild(1),
        )
        for state in (first, second, other):
            await manager.stop(state)
        return first, second, found

    first, second, (by_voice, missing, in_guild) = asyncio.run(scenario())
    assert by_voice is second
    assert missing is None
    assert in_guild == [first, second]


# ---- skip / stop ----

def test_skip_without_current_track_returns_false():
    async def scenario():
        manager = MusicManager(source=FakeSource())
        session = FakeSession()
        state = manager.start(session, None)
        result = manager.skip(state)
        await manager.stop(state)
        return result, session

    result, session = asyncio.run(scenario())
    assert result is False


def test_skip_moves_on_to_the_next_track():
    async def scenario():
        manager = MusicManager(source=FakeSource())
        session = FakeSession(auto_finish=False)
        state = manager.start(session, FakeTextChannel())
        with pcm_audio():
            manager.enqueue(state, [track("a"), track("b")])
            await wait_for(lambda: len(session.played) == 1)
            assert manager.skip(state) is True
            await wait_for(lambda: len(session.played) == 2)
            await manager.stop(state)
        return session

    session = asyncio.run(scenario())
    assert [audio.url for audio in session.played] == [
        "https://stream.example.com/a",
        "https://stream.example.com/b",
    ]


def test_stop_releases_music_and_forgets_state():
    async def scenario():
        manager = MusicManager(source=FakeSource())
        session = FakeSession(auto_finish=False)
        state = manager.start(session, FakeTextChannel())
        with pcm_audio():
            manager.enqueue(state, [track("a"), track("b")])
            await wait_for(lambda: len(session.played) == 1)
            await manager.stop(state)
            # 2 回目は何もしない(接続を二重に手放さない)
            await manager.stop(state)
        return manager, session, state

    manager, session, state = asyncio.run(scenario())
    assert manager.state_for_voice(10) is None
    assert session.released == [USE_MUSIC]
    assert session.skips >= 1
    assert len(state.queue) == 0


# ---- ワーカー ----

def test_worker_plays_queued_tracks_in_order():
    async def scenario():
        source = FakeSource()
        session = FakeSession()
        channel = FakeTextChannel()
        manager = MusicManager(source=source)
        with pcm_audio():
            state = manager.start(session, channel)
            manager.enqueue(state, [track("a"), track("b")])
            await wait_for(lambda: len(session.played) == 2)
            await manager.stop(state)
        return source, channel

    source, channel = asyncio.run(scenario())
    assert source.resolved == ["a", "b"]
    assert channel.sent[0].startswith("▶️ 再生中: a")


def test_worker_continues_after_a_failing_track():
    # 1 曲の失敗で止めず、理由を伝えて次の曲へ進む
    async def scenario():
        source = FakeSource(failing_titles={"bad"})
        session = FakeSession()
        channel = FakeTextChannel()
        manager = MusicManager(source=source)
        with pcm_audio():
            state = manager.start(session, channel)
            manager.enqueue(state, [track("bad"), track("good")])
            await wait_for(lambda: source.resolved == ["good"])
            await manager.stop(state)
        return source, channel

    source, channel = asyncio.run(scenario())
    assert source.resolved == ["good"]
    assert "「bad」の再生に失敗しました。" in channel.sent


def test_worker_releases_music_after_idle_timeout():
    # キューが空のまま放置されたら音楽をやめる。読み上げが使っていれば接続は残る
    async def scenario():
        session = FakeSession()
        channel = FakeTextChannel()
        manager = MusicManager(source=FakeSource())
        with patch("src.music_manager.IDLE_TIMEOUT_SECONDS", 0.01):
            manager.start(session, channel)
            await wait_for(lambda: session.released)
        return manager, session, channel

    manager, session, channel = asyncio.run(scenario())
    assert session.released == [USE_MUSIC]
    assert manager.state_for_voice(10) is None
    assert "キューが空になったので音楽を終了します。" in channel.sent


def test_track_added_at_timeout_is_still_played():
    # 待ち時間切れと同時に積まれた曲を捨てない
    async def scenario():
        session = FakeSession()
        manager = MusicManager(source=FakeSource())
        real_wait_for = asyncio.wait_for
        calls = []

        async def racing_wait_for(awaitable, timeout):
            calls.append(timeout)
            if len(calls) == 1:
                awaitable.close()
                state.queue.append(track("late"))
                raise asyncio.TimeoutError
            return await real_wait_for(awaitable, timeout)

        with pcm_audio(), patch("src.music_manager.asyncio.wait_for", racing_wait_for):
            state = manager.start(session, None)
            await wait_for(lambda: len(session.played) == 1)
            await manager.stop(state)
        return session

    session = asyncio.run(scenario())
    assert [audio.url for audio in session.played] == ["https://stream.example.com/late"]
