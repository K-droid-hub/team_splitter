import asyncio
from unittest.mock import Mock, patch

from src.tts_engine import VoicevoxEngine, sanitize_text
from src.tts_manager import GuildTtsState, TtsManager
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

def make_state(channel_id=100, notify=False):
    return GuildTtsState(
        text_channel_id=channel_id,
        voice_client=Mock(),
        notify_join_leave=notify,
    )


def test_is_target_channel_matches_only_registered_channel():
    mgr = TtsManager(engine=FakeEngine(), store=make_store())
    mgr.guild_states[1] = make_state(channel_id=100)
    assert mgr.is_target_channel(1, 100) is True
    assert mgr.is_target_channel(1, 999) is False
    assert mgr.is_target_channel(2, 100) is False


def test_should_notify_reflects_flag():
    mgr = TtsManager(engine=FakeEngine(), store=make_store())
    mgr.guild_states[1] = make_state(notify=True)
    mgr.guild_states[2] = make_state(notify=False)
    assert mgr.should_notify(1) is True
    assert mgr.should_notify(2) is False
    assert mgr.should_notify(3) is False


def test_get_speaker_falls_back_to_default_then_uses_setting():
    mgr = TtsManager(engine=FakeEngine(default_speaker=3), store=make_store())
    assert mgr.get_speaker(user_id=42) == 3
    mgr.set_speaker(user_id=42, speaker_id=8)
    assert mgr.get_speaker(user_id=42) == 8


def test_enqueue_message_prefixes_nickname_and_sanitizes():
    mgr = TtsManager(engine=FakeEngine(default_speaker=3), store=make_store())
    mgr.guild_states[1] = make_state()
    mgr.set_speaker(user_id=42, speaker_id=8)
    mgr.enqueue_message(1, "やあ https://x.com", user_id=42, display_name="たろう")
    text, speaker = mgr.guild_states[1].queue.get_nowait()
    # 発言者ニックネームを先頭に付け、本文をサニタイズすること
    assert text == "たろう、やあ URL 省略"
    assert speaker == 8


def test_enqueue_skips_when_empty_after_sanitize():
    mgr = TtsManager(engine=FakeEngine(), store=make_store())
    mgr.guild_states[1] = make_state()
    # 本文が空になるなら、名前だけ読み上げず積まないこと
    mgr.enqueue_message(1, "<@123>", user_id=42, display_name="たろう")
    assert mgr.guild_states[1].queue.empty()


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
        vc = Mock()
        vc.is_connected.return_value = True
        vc.disconnect = Mock(return_value=asyncio.sleep(0))
        mgr.start_reading(1, 100, vc, notify_join_leave=True)
        assert mgr.is_reading(1) is True
        assert mgr.is_target_channel(1, 100) is True
        await mgr.stop_reading(1)
        assert mgr.is_reading(1) is False
        vc.disconnect.assert_called_once()

    asyncio.run(scenario())


def test_play_one_synthesizes_and_plays_with_speaker():
    async def scenario():
        engine = FakeEngine()
        mgr = TtsManager(engine=engine, store=make_store())
        vc = Mock()
        # play は完了コールバックを即座に呼び、再生完了を模擬する
        vc.play = Mock(side_effect=lambda source, after: after(None))
        state = GuildTtsState(text_channel_id=1, voice_client=vc)
        with patch("src.tts_manager.discord.FFmpegOpusAudio", return_value=Mock()):
            await mgr._play_one(state, "こんにちは", 5)
        assert engine.calls == [("こんにちは", 5)]
        vc.play.assert_called_once()

    asyncio.run(scenario())


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
        # synthesis には audio_query の結果 JSON を渡すこと
        assert session.calls[1][2] == {"q": 1}

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
