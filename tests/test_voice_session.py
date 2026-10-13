import asyncio
import struct
from types import SimpleNamespace

from src.audio_mixer import FRAME_SIZE
from src.voice_session import USE_MUSIC, USE_TTS, VoiceSession


class FakeVoiceClient:
    def __init__(self, channel_id=10, guild_id=1, play_error=None):
        self.guild = SimpleNamespace(id=guild_id)
        self.channel = SimpleNamespace(id=channel_id, name=f"vc{channel_id}")
        self.connected = True
        self.playing = False
        self.play_calls = []
        self.play_error = play_error
        self.disconnects = 0

    def is_connected(self):
        return self.connected

    def is_playing(self):
        return self.playing

    def play(self, source, after=None):
        if self.play_error is not None:
            raise self.play_error
        self.play_calls.append((source, after))
        self.playing = True

    async def disconnect(self):
        self.disconnects += 1
        self.connected = False


def pcm(frames):
    return struct.pack("<h", 100) * (FRAME_SIZE // 2) * frames


async def drain(session):
    # 送信スレッドの代わりに、ミキサーを空になるまで読み進める
    while session.mixer.read():
        pass
    await asyncio.sleep(0)


def test_play_clip_starts_sending_and_waits_until_heard():
    async def scenario():
        voice_client = FakeVoiceClient()
        session = VoiceSession(voice_client)
        task = asyncio.create_task(session.play_clip(pcm(2)))
        await asyncio.sleep(0)
        assert [call[0] for call in voice_client.play_calls] == [session.mixer]
        assert task.done() is False
        await drain(session)
        await asyncio.wait_for(task, 1)

    asyncio.run(scenario())


def test_does_not_restart_while_already_sending():
    # 同じ接続で play() を重ねて呼ぶと discord.py が例外を出すので、鳴っている間は呼ばない
    async def scenario():
        voice_client = FakeVoiceClient()
        session = VoiceSession(voice_client)
        first = asyncio.create_task(session.play_clip(pcm(1)))
        second = asyncio.create_task(session.play_clip(pcm(1)))
        await asyncio.sleep(0)
        assert len(voice_client.play_calls) == 1
        await drain(session)
        await asyncio.wait_for(asyncio.gather(first, second), 1)

    asyncio.run(scenario())


def test_restarts_when_audio_arrived_while_stopping():
    # 送信スレッドが止まる直前に積まれた音を取りこぼさない
    async def scenario():
        voice_client = FakeVoiceClient()
        session = VoiceSession(voice_client)
        session.mixer.add_clip(pcm(1), lambda: None)
        voice_client.playing = False
        session._after(None)
        await asyncio.sleep(0)
        assert len(voice_client.play_calls) == 1

    asyncio.run(scenario())


def test_stays_quiet_when_nothing_is_pending():
    async def scenario():
        voice_client = FakeVoiceClient()
        session = VoiceSession(voice_client)
        session._after(None)
        await asyncio.sleep(0)
        assert voice_client.play_calls == []

    asyncio.run(scenario())


def test_failed_start_does_not_leave_waiters_hanging():
    # 送信を始められなかったら、待っている読み上げを起こして先へ進ませる
    async def scenario():
        voice_client = FakeVoiceClient(play_error=RuntimeError("Opus が読み込めません"))
        session = VoiceSession(voice_client)
        await asyncio.wait_for(session.play_clip(pcm(1)), 1)
        assert session.mixer.has_pending() is False

    asyncio.run(scenario())


def test_play_clip_on_disconnected_session_returns_immediately():
    async def scenario():
        voice_client = FakeVoiceClient()
        voice_client.connected = False
        session = VoiceSession(voice_client)
        await asyncio.wait_for(session.play_clip(pcm(1)), 1)
        assert voice_client.play_calls == []

    asyncio.run(scenario())


def test_release_disconnects_only_after_last_user():
    # 読み上げと音楽の両方が手放したときだけ切断して枠を返す
    async def scenario():
        voice_client = FakeVoiceClient()
        closed = []
        session = VoiceSession(voice_client, on_close=closed.append)
        session.retain(USE_TTS)
        session.retain(USE_MUSIC)
        await session.release(USE_TTS)
        assert voice_client.disconnects == 0
        assert session.is_alive() is True
        await session.release(USE_MUSIC)
        assert voice_client.disconnects == 1
        assert closed == [session]
        assert session.is_alive() is False

    asyncio.run(scenario())


def test_close_wakes_a_waiting_track():
    async def scenario():
        voice_client = FakeVoiceClient()
        session = VoiceSession(voice_client)

        class Source:
            def __init__(self):
                self.cleaned = 0

            def read(self):
                # ffmpeg の読み込み待ちを模す。止められるまで何も返さない
                import time

                time.sleep(0.01)
                return b"" if self.cleaned else struct.pack("<h", 1) * (FRAME_SIZE // 2)

            def cleanup(self):
                self.cleaned += 1

        task = asyncio.create_task(session.play_track(Source()))
        await asyncio.sleep(0)
        await session.close()
        await asyncio.wait_for(task, 1)

    asyncio.run(scenario())


def test_follows_the_bot_when_moved_to_another_channel():
    # bot がドラッグで移されても、いまいるチャンネルで判定する
    async def scenario():
        voice_client = FakeVoiceClient(channel_id=10)
        session = VoiceSession(voice_client)
        voice_client.channel = SimpleNamespace(id=20, name="vc20")
        assert session.channel_id == 20

    asyncio.run(scenario())
