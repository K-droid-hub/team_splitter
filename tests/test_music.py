import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from src.modules.music import Music
from src.music_manager import MAX_QUEUE_SIZE, MusicManager, MusicState
from src.music_source import Track
from src.voice_session import USE_MUSIC


def make_cog():
    bot = SimpleNamespace(music_manager=Mock(), voice_pool=Mock())
    return Music(bot)


def track(title):
    return Track(title=title, webpage_url=f"https://example.com/{title}")


# ---- リピート ----

def test_repeat_puts_current_track_next_in_the_queue():
    manager = MusicManager(source=Mock())
    state = add_state(manager, 10)
    state.current = track("a")
    state.queue.append(track("b"))
    assert manager.repeat_next(state) is True
    assert [t.title for t in state.queue] == ["a", "b"]


def test_repeat_does_nothing_when_nothing_is_playing():
    manager = MusicManager(source=Mock())
    state = add_state(manager, 10)
    assert manager.repeat_next(state) is False
    assert len(state.queue) == 0


def test_repeat_refused_when_queue_is_full():
    manager = MusicManager(source=Mock())
    state = add_state(manager, 10)
    state.current = track("a")
    state.queue.extend(track(str(i)) for i in range(MAX_QUEUE_SIZE))
    assert manager.repeat_next(state) is False


# ---- 追加結果の文面 ----

def test_message_for_a_single_track_names_it():
    cog = make_cog()
    assert cog._build_added_message([track("a")], 1) == "キューに追加しました: a"


def test_message_for_multiple_tracks_shows_the_count():
    cog = make_cog()
    assert cog._build_added_message([track("a"), track("b")], 2) == "キューに 2 件追加しました。"


def test_message_mentions_truncated_tracks():
    # 上限で切り捨てた分を黙って捨てない
    cog = make_cog()
    message = cog._build_added_message([track(str(i)) for i in range(5)], 3)
    assert "2 件は追加できませんでした" in message


def test_message_when_nothing_could_be_added():
    cog = make_cog()
    message = cog._build_added_message([track("a")], 0)
    assert str(MAX_QUEUE_SIZE) in message


# ---- 操作対象の決め方 ----

class FakeSession:
    def __init__(self, channel_id=10, guild_id=1):
        self.channel_id = channel_id
        self.guild_id = guild_id
        self.released = []
        self.skips = 0

    def is_alive(self):
        return True

    def skip_track(self):
        self.skips += 1

    async def release(self, user):
        self.released.append(user)


def add_state(manager, channel_id, guild_id=1):
    session = FakeSession(channel_id, guild_id)
    state = MusicState(session=session, text_channel=None)
    manager.states[session] = state
    return state


def make_ctx(voice_channel_id=None, guild_id=1):
    voice = None
    if voice_channel_id is not None:
        voice = SimpleNamespace(channel=SimpleNamespace(id=voice_channel_id))
    return SimpleNamespace(
        guild=SimpleNamespace(id=guild_id),
        author=SimpleNamespace(id=111, voice=voice),
    )


def make_routing_cog():
    manager = MusicManager(source=Mock())
    bot = SimpleNamespace(music_manager=manager, voice_pool=Mock())
    return Music(bot), manager


def test_targets_the_channel_the_user_is_in():
    cog, manager = make_routing_cog()
    add_state(manager, 10)
    mine = add_state(manager, 11)
    assert cog._target_state(make_ctx(voice_channel_id=11)) == (mine, None)


def test_outside_voice_cannot_control_playback():
    # VC に入っていない人は、サーバーに再生が 1 か所しかなくても操作できない
    cog, manager = make_routing_cog()
    add_state(manager, 10)
    state, error = cog._target_state(make_ctx())
    assert state is None
    assert "入ってから" in error


def test_cannot_control_another_channels_playback():
    cog, manager = make_routing_cog()
    add_state(manager, 10)
    state, error = cog._target_state(make_ctx(voice_channel_id=11))
    assert state is None
    assert "再生していません" in error


def test_reports_when_nothing_is_playing_here():
    cog, _ = make_routing_cog()
    state, error = cog._target_state(make_ctx(voice_channel_id=10))
    assert state is None
    assert error == "このボイスチャンネルでは再生していません。"


def test_queue_message_lists_current_and_upcoming():
    cog, manager = make_routing_cog()
    state = add_state(manager, 10)
    state.current = track("now")
    state.queue.extend([Track(title="next", webpage_url="u", duration=75, requested_by="たろう")])
    message = cog._build_queue_message(state)
    assert message.splitlines() == ["▶️ 再生中: now", "1. next [1:15] / たろう"]


# ---- 無人になったら音楽をやめる ----

def make_listener_cog(playing_channel_id=10, members=()):
    manager = MusicManager(source=Mock())
    state = None
    if playing_channel_id is not None:
        state = add_state(manager, playing_channel_id)
    bot = SimpleNamespace(
        music_manager=manager,
        voice_pool=Mock(),
        get_channel=lambda channel_id: SimpleNamespace(
            id=channel_id, members=list(members)
        ),
    )
    return Music(bot), manager, state


def leave_event(member_is_bot=False, before_id=10, after_id=None):
    member = SimpleNamespace(bot=member_is_bot, guild=SimpleNamespace(id=1))
    before = SimpleNamespace(
        channel=None if before_id is None else SimpleNamespace(id=before_id)
    )
    after = SimpleNamespace(
        channel=None if after_id is None else SimpleNamespace(id=after_id)
    )
    return member, before, after


def test_stops_music_when_nobody_is_left():
    cog, manager, state = make_listener_cog(members=[])
    asyncio.run(cog.on_voice_state_update(*leave_event()))
    assert manager.state_for_voice(10) is None
    assert state.session.released == [USE_MUSIC]


def test_keeps_playing_while_someone_stays():
    cog, manager, state = make_listener_cog(members=[SimpleNamespace(bot=False)])
    asyncio.run(cog.on_voice_state_update(*leave_event()))
    assert manager.state_for_voice(10) is state


def test_bot_only_channel_counts_as_empty():
    # 2 体目の bot だけが残った状態は無人として扱う
    cog, manager, _ = make_listener_cog(members=[SimpleNamespace(bot=True)])
    asyncio.run(cog.on_voice_state_update(*leave_event()))
    assert manager.state_for_voice(10) is None


def test_ignores_bot_own_state_change():
    cog, manager, state = make_listener_cog(members=[])
    asyncio.run(cog.on_voice_state_update(*leave_event(member_is_bot=True)))
    assert manager.state_for_voice(10) is state


def test_ignores_other_channels():
    cog, manager, state = make_listener_cog(playing_channel_id=10, members=[])
    asyncio.run(cog.on_voice_state_update(*leave_event(before_id=99)))
    assert manager.state_for_voice(10) is state


def test_ignores_mute_and_unmute_in_the_same_channel():
    # ミュート切り替えでは channel が変わらないので退出判定に入れない
    cog, manager, state = make_listener_cog(members=[])
    asyncio.run(cog.on_voice_state_update(*leave_event(before_id=10, after_id=10)))
    assert manager.state_for_voice(10) is state


def test_does_nothing_without_a_session():
    cog, manager, _ = make_listener_cog(playing_channel_id=None, members=[])
    asyncio.run(cog.on_voice_state_update(*leave_event()))
    assert manager.states == {}
