import asyncio
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("GUILD_IDS", "0")  # modules.tts の import 時に GUILD_IDS を参照するため

from src.modules.tts import Tts
from src.tts_engine import VOICE_VOLUME_SCALE, VoicevoxEngine, sanitize_text
from src.tts_manager import SAMPLE_VOICE_TEXT, TtsManager, TtsState
from src.voice_session import USE_TTS
from src.user_voice_store import UserVoiceStore


# テスト用に、ファイルを作らないインメモリ DB のストアを返す
def make_store():
    return UserVoiceStore(db_path=":memory:")


class FakeEngine:
    def __init__(self, default_speaker=3):
        self.default_speaker = default_speaker
        self.calls = []

    async def synthesize_speech(self, text, speaker=None):
        self.calls.append((text, speaker))
        return b"wav"


# ---- sanitize_text ----

def test_sanitize_text_replaces_url():
    assert sanitize_text("見て https://example.com/a?b=1 ここ") == "見て URL 省略 ここ"


def test_sanitize_text_removes_mention_and_emoji():
    assert sanitize_text("<@123> やあ <:smile:456>") == "やあ"


def test_sanitize_text_truncates_long_text():
    result = sanitize_text("あ" * 150)
    assert result == "あ" * 100 + " 以下省略"


# ---- TtsManager: 状態・話者 ----

class FakeVoiceSession:
    """VoiceSession の代わり。流した PCM と手放した機能を記録する"""

    def __init__(self, channel_id=10, guild_id=1):
        self.channel_id = channel_id
        self.guild_id = guild_id
        self.clips = []
        self.released = []
        self.cleared = 0

    def is_alive(self):
        return True

    async def play_clip(self, pcm):
        self.clips.append(pcm)

    def clear_clips(self):
        self.cleared += 1

    async def release(self, user):
        self.released.append(user)


def make_state(text_channel_id=100, voice_channel_id=10, guild_id=1, notify=False):
    return TtsState(
        guild_id=guild_id,
        text_channel_id=text_channel_id,
        session=FakeVoiceSession(voice_channel_id, guild_id),
        notify_join_leave=notify,
    )


def test_is_target_channel_matches_only_registered_channel():
    mgr = TtsManager(engine=FakeEngine(), store=make_store())
    mgr.states[100] = make_state(text_channel_id=100)
    assert mgr.is_target_channel(1, 100) is True
    assert mgr.is_target_channel(1, 999) is False
    assert mgr.is_target_channel(2, 100) is False


def test_two_voice_channels_can_read_in_one_server():
    # 同じサーバーでも、ボイスチャンネルが違えば別々のテキストチャンネルを読み上げられる
    mgr = TtsManager(engine=FakeEngine(), store=make_store())
    mgr.states[100] = make_state(text_channel_id=100, voice_channel_id=10)
    mgr.states[200] = make_state(text_channel_id=200, voice_channel_id=11)
    assert mgr.is_target_channel(1, 100) is True
    assert mgr.is_target_channel(1, 200) is True
    assert mgr.is_reading(1) is True
    assert mgr.is_reading(2) is False


def test_state_for_voice_finds_the_reading_in_that_channel():
    mgr = TtsManager(engine=FakeEngine(), store=make_store())
    mgr.states[100] = make_state(text_channel_id=100, voice_channel_id=10)
    mgr.states[200] = make_state(text_channel_id=200, voice_channel_id=11)
    assert mgr.state_for_voice(11).text_channel_id == 200
    assert mgr.state_for_voice(99) is None


def test_synthesize_sample_uses_fixed_text_and_given_speaker():
    async def scenario():
        engine = FakeEngine()
        mgr = TtsManager(engine=engine, store=make_store())
        wav = await mgr.synthesize_sample(speaker=8)
        # 試聴は VC 接続を伴わず、固定サンプル文を指定話者で合成すること
        assert engine.calls == [(SAMPLE_VOICE_TEXT, 8)]
        assert wav == b"wav"

    asyncio.run(scenario())


def test_get_speaker_falls_back_to_default_then_uses_setting():
    mgr = TtsManager(engine=FakeEngine(default_speaker=3), store=make_store())
    assert mgr.get_speaker(user_id=42) == 3
    mgr.set_speaker(user_id=42, speaker_id=8)
    assert mgr.get_speaker(user_id=42) == 8


def test_enqueue_message_prefixes_nickname_and_sanitizes():
    mgr = TtsManager(engine=FakeEngine(default_speaker=3), store=make_store())
    mgr.states[100] = make_state()
    mgr.set_speaker(user_id=42, speaker_id=8)
    mgr.enqueue_message(100, "やあ https://x.com", user_id=42, display_name="たろう")
    text, speaker = mgr.states[100].queue.get_nowait()
    # 発言者ニックネームを先頭に付け、本文をサニタイズすること
    assert text == "たろう、やあ URL 省略"
    assert speaker == 8


def test_enqueue_skips_when_empty_after_sanitize():
    mgr = TtsManager(engine=FakeEngine(), store=make_store())
    mgr.states[100] = make_state()
    # 本文が空になるなら、名前だけ読み上げず積まないこと
    mgr.enqueue_message(100, "<@123>", user_id=42, display_name="たろう")
    assert mgr.states[100].queue.empty()


def test_enqueue_ignored_when_not_reading():
    mgr = TtsManager(engine=FakeEngine(), store=make_store())
    # 例外を出さず、単に無視されること
    mgr.enqueue_message(999, "test", user_id=1, display_name="foo")


# ---- 話者設定の永続化 ----

def test_set_speaker_persists_and_reloads_into_new_manager():
    # 同一 DB を共有し、再起動で TtsManager を作り直した状況を模擬する
    store = make_store()
    mgr = TtsManager(engine=FakeEngine(), store=store)
    mgr.set_speaker(user_id=42, speaker_id=8, display_name="たろう")

    # 同じ DB から作り直した TtsManager に設定が復元されること
    reloaded = TtsManager(engine=FakeEngine(), store=store)
    assert reloaded.get_speaker(user_id=42) == 8


def test_store_set_speaker_upserts_latest_value():
    store = make_store()
    store.set_speaker(user_id=1, speaker_id=2, display_name="a")
    store.set_speaker(user_id=1, speaker_id=9, display_name="a")
    # 同一ユーザーは重複せず最新の話者で上書きされること
    assert store.load_all_speakers() == {1: 9}


def test_store_persists_to_file_across_connections(tmp_path):
    db_path = str(tmp_path / "voices.db")
    UserVoiceStore(db_path=db_path).set_speaker(user_id=7, speaker_id=3)
    # 別接続 (= コンテナ再起動相当) で開き直しても残っていること
    assert UserVoiceStore(db_path=db_path).load_all_speakers() == {7: 3}


# ---- TtsManager: ライフサイクル ----

def test_start_and_stop_reading_lifecycle():
    async def scenario():
        mgr = TtsManager(engine=FakeEngine(), store=make_store())
        session = FakeVoiceSession()
        mgr.start_reading(100, session, notify_join_leave=True)
        assert mgr.is_reading(1) is True
        assert mgr.is_target_channel(1, 100) is True
        assert mgr.states[100].notify_join_leave is True
        await mgr.stop_reading(100)
        assert mgr.is_reading(1) is False
        # 読み上げを手放すだけ。音楽も使っていれば接続は残る
        assert session.released == [USE_TTS]
        assert session.cleared == 1

    asyncio.run(scenario())


def make_wav(samples=240, rate=24000):
    import io
    import struct
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(struct.pack("<h", 1000) * samples)
    return buffer.getvalue()


class WavEngine(FakeEngine):
    # ミキサーへ渡す前に WAV を解釈するので、本物と同じ形の WAV を返す
    async def synthesize_speech(self, text, speaker=None):
        self.calls.append((text, speaker))
        return make_wav()


def test_play_one_synthesizes_and_mixes_as_discord_pcm():
    async def scenario():
        engine = WavEngine()
        mgr = TtsManager(engine=engine, store=make_store())
        state = make_state()
        await mgr._play_one(state, "こんにちは", 5)
        return engine, state

    engine, state = asyncio.run(scenario())
    assert engine.calls == [("こんにちは", 5)]
    # 24kHz・モノラル 240 サンプルが、48kHz・ステレオ 480 サンプル(1920 バイト)前後になる
    assert len(state.session.clips) == 1
    assert abs(len(state.session.clips[0]) - 1920) <= 8


def test_worker_reads_messages_in_order():
    async def scenario():
        engine = WavEngine()
        mgr = TtsManager(engine=engine, store=make_store())
        session = FakeVoiceSession()
        mgr.start_reading(100, session, notify_join_leave=False)
        mgr.enqueue_message(100, "一つ目", user_id=1, display_name="a")
        mgr.enqueue_notice(100, "二つ目")
        await asyncio.wait_for(mgr.states[100].queue.join(), 1)
        await mgr.stop_reading(100)
        return [call[0] for call in engine.calls], session

    spoken, session = asyncio.run(scenario())
    assert spoken == ["a、一つ目", "二つ目"]
    assert len(session.clips) == 2


# ---- VoicevoxEngine.synthesize_speech ----

class FakeResponse:
    def __init__(self, json_data=None, read_data=None):
        self._json = json_data
        self._read = read_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def raise_for_status(self):
        pass

    async def json(self):
        return self._json

    async def read(self):
        return self._read


class FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def post(self, url, params=None, json=None):
        self.calls.append((url, params, json))
        return self.responses.pop(0)


def test_synthesize_speech_calls_query_then_synthesis():
    async def scenario():
        engine = VoicevoxEngine(base_url="http://voicevox:50021", default_speaker=3)
        session = FakeSession([
            FakeResponse(json_data={"q": 1}),
            FakeResponse(read_data=b"AUDIO"),
        ])
        with patch("src.tts_engine.aiohttp.ClientSession", return_value=session):
            wav = await engine.synthesize_speech("やあ", speaker=5)
        assert wav == b"AUDIO"
        # audio_query -> synthesis の順で、話者 ID が渡ること
        assert session.calls[0][0].endswith("/audio_query")
        assert session.calls[0][1]["speaker"] == 5
        assert session.calls[1][0].endswith("/synthesis")
        assert session.calls[1][1]["speaker"] == 5
        # synthesis には audio_query の結果 JSON を渡すこと。音量だけ上げて渡す
        assert session.calls[1][2] == {"q": 1, "volumeScale": VOICE_VOLUME_SCALE}

    asyncio.run(scenario())


def test_synthesize_speech_uses_default_speaker_when_unspecified():
    async def scenario():
        engine = VoicevoxEngine(base_url="http://voicevox:50021", default_speaker=7)
        session = FakeSession([
            FakeResponse(json_data={}),
            FakeResponse(read_data=b"AUDIO"),
        ])
        with patch("src.tts_engine.aiohttp.ClientSession", return_value=session):
            await engine.synthesize_speech("やあ")
        assert session.calls[0][1]["speaker"] == 7

    asyncio.run(scenario())


# ---- コマンドを読み上げ対象から外す ----

def make_tts_cog(prefix="!"):
    bot = SimpleNamespace(command_prefix=prefix, tts_manager=Mock())
    return Tts(bot)


def test_prefixed_message_is_not_read():
    # !play などのコマンドを読み上げても意味がない
    assert make_tts_cog()._is_command("!play https://example.com") is True


def test_plain_message_is_read():
    cog = make_tts_cog()
    assert cog._is_command("こんにちは") is False
    assert cog._is_command("えっ!すごい") is False


def test_multiple_prefixes_are_handled():
    cog = make_tts_cog(prefix=("!", "?"))
    assert cog._is_command("?help") is True
    assert cog._is_command("やあ") is False


def test_callable_prefix_keeps_the_message_readable():
    # 関数形式のプレフィックスには対応しないので、読み上げ対象のままにする
    cog = make_tts_cog(prefix=lambda bot, message: "!")
    assert cog._is_command("!play x") is False


# ---- 読み上げの開始(コマンドとパネルで共通) ----

from unittest.mock import AsyncMock

from src.modules.tts import begin_reading
from src.voice_pool import VoiceSlotUnavailable


def make_begin_bot(existing=None, acquire=None):
    manager = TtsManager(engine=FakeEngine(), store=make_store())
    if existing is not None:
        manager.states[existing.text_channel_id] = existing
    pool = SimpleNamespace(acquire=acquire or AsyncMock(return_value=FakeVoiceSession()))
    return SimpleNamespace(tts_manager=manager, voice_pool=pool)


def test_begin_reading_starts_on_the_acquired_session():
    async def scenario():
        bot = make_begin_bot()
        error = await begin_reading(bot, 100, SimpleNamespace(id=10), True)
        return bot, error

    bot, error = asyncio.run(scenario())
    assert error is None
    assert bot.tts_manager.is_target_channel(1, 100) is True
    bot.voice_pool.acquire.assert_awaited_once()
    assert bot.voice_pool.acquire.await_args.args[1] == USE_TTS


def test_begin_reading_refuses_a_voice_channel_reading_elsewhere():
    # 1 つのボイスチャンネルで 2 つのテキストチャンネルを読むと、誰の発言か分からなくなる
    async def scenario():
        bot = make_begin_bot(existing=make_state(text_channel_id=555, voice_channel_id=10))
        error = await begin_reading(bot, 100, SimpleNamespace(id=10), False)
        return bot, error

    bot, error = asyncio.run(scenario())
    assert "<#555>" in error
    bot.voice_pool.acquire.assert_not_awaited()


def test_begin_reading_yields_when_another_channel_wins_the_race():
    # 接続を待つ間に別のテキストチャンネルが同じボイスチャンネルで始めていたら譲る。
    # 相手が読み上げとして接続を使っているので、手放してはいけない
    async def scenario():
        holder = {}

        async def acquire(channel, user):
            state = make_state(text_channel_id=555, voice_channel_id=10)
            holder["bot"].tts_manager.states[555] = state
            return state.session

        bot = make_begin_bot(acquire=acquire)
        holder["bot"] = bot
        error = await begin_reading(bot, 100, SimpleNamespace(id=10), False)
        return bot, error

    bot, error = asyncio.run(scenario())
    assert "<#555>" in error
    assert bot.tts_manager.is_target_channel(1, 100) is False
    assert bot.tts_manager.states[555].session.released == []


def test_begin_reading_reports_when_no_slot_is_free():
    async def scenario():
        bot = make_begin_bot(
            acquire=AsyncMock(side_effect=VoiceSlotUnavailable("1 つまでです"))
        )
        return await begin_reading(bot, 100, SimpleNamespace(id=10), False)

    assert asyncio.run(scenario()) == "1 つまでです"


# ---- ボイスチャンネルごとの入退室・自動退出 ----

def make_voice_cog(states, members=()):
    manager = TtsManager(engine=FakeEngine(), store=make_store())
    for state in states:
        manager.states[state.text_channel_id] = state
    panel = SimpleNamespace(refresh_panels=AsyncMock())
    bot = SimpleNamespace(
        command_prefix="!",
        tts_manager=manager,
        get_channel=lambda channel_id: SimpleNamespace(id=channel_id, members=list(members)),
        get_cog=lambda name: panel,
    )
    return Tts(bot), manager, panel


def move(before_id, after_id, name="はなこ", is_bot=False):
    member = SimpleNamespace(bot=is_bot, display_name=name, guild=SimpleNamespace(id=1))
    channel = lambda cid: None if cid is None else SimpleNamespace(id=cid)
    return member, SimpleNamespace(channel=channel(before_id)), SimpleNamespace(channel=channel(after_id))


def queued_texts(state):
    texts = []
    while not state.queue.empty():
        texts.append(state.queue.get_nowait()[0])
    return texts


def test_join_and_leave_are_announced_in_each_voice_channel():
    # 移動すると、出た側では退室・入った側では入室として、それぞれの読み上げに流れる
    first = make_state(text_channel_id=100, voice_channel_id=10, notify=True)
    second = make_state(text_channel_id=200, voice_channel_id=11, notify=True)
    cog, _, _ = make_voice_cog([first, second], members=[SimpleNamespace(bot=False)])
    asyncio.run(cog.on_voice_state_update(*move(10, 11)))
    assert queued_texts(first) == ["はなこ が退室しました"]
    assert queued_texts(second) == ["はなこ が入室しました"]


def test_announcements_follow_each_channel_setting():
    quiet = make_state(text_channel_id=100, voice_channel_id=10, notify=False)
    cog, _, _ = make_voice_cog([quiet], members=[SimpleNamespace(bot=False)])
    asyncio.run(cog.on_voice_state_update(*move(None, 10)))
    assert queued_texts(quiet) == []


def test_reading_stops_when_its_voice_channel_empties():
    state = make_state(text_channel_id=100, voice_channel_id=10)
    other = make_state(text_channel_id=200, voice_channel_id=11)
    cog, manager, panel = make_voice_cog([state, other], members=[])
    asyncio.run(cog.on_voice_state_update(*move(10, None)))
    # 無人になったチャンネルの読み上げだけを止め、もう一方は続ける
    assert manager.is_target_channel(1, 100) is False
    assert manager.is_target_channel(1, 200) is True
    assert state.session.released == [USE_TTS]
    panel.refresh_panels.assert_awaited_once()


def test_mute_toggle_is_ignored():
    state = make_state(text_channel_id=100, voice_channel_id=10, notify=True)
    cog, manager, _ = make_voice_cog([state], members=[])
    asyncio.run(cog.on_voice_state_update(*move(10, 10)))
    assert queued_texts(state) == []
    assert manager.is_target_channel(1, 100) is True


# ---- /読み上げ終了 の対象 ----

def make_stop_interaction(channel_id, voice_channel_id=None):
    voice = None if voice_channel_id is None else SimpleNamespace(channel=SimpleNamespace(id=voice_channel_id))
    return SimpleNamespace(guild_id=1, channel_id=channel_id, user=SimpleNamespace(voice=voice))


def test_stop_targets_this_text_channel_first():
    cog, _, _ = make_voice_cog([make_state(text_channel_id=100, voice_channel_id=10)])
    assert cog._reading_target(make_stop_interaction(100, voice_channel_id=11)) == 100


def test_stop_falls_back_to_the_reading_in_your_voice_channel():
    # 読み上げ対象以外のチャンネルで実行しても、自分のいるボイスチャンネルの読み上げを止められる
    cog, _, _ = make_voice_cog([make_state(text_channel_id=100, voice_channel_id=10)])
    assert cog._reading_target(make_stop_interaction(999, voice_channel_id=10)) == 100


def test_stop_has_no_target_outside_any_reading():
    cog, _, _ = make_voice_cog([make_state(text_channel_id=100, voice_channel_id=10)])
    assert cog._reading_target(make_stop_interaction(999)) is None



def test_join_leave_notice_defaults_off_and_can_be_switched():
    manager = TtsManager(engine=Mock(), store=Mock())
    assert manager.join_leave_notice(5) is False
    manager.set_join_leave_notice(5, True)
    assert manager.join_leave_notice(5) is True
    # 他のチャンネルの設定には影響しない
    assert manager.join_leave_notice(6) is False
