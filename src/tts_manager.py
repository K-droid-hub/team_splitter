import asyncio
import dataclasses
import io

import discord

from src.tts_engine import VoicevoxEngine, sanitize_text
from src.user_voice_store import UserVoiceStore

# 声設定 UI の試聴で読み上げる固定サンプル文
SAMPLE_VOICE_TEXT = "こんにちは、この声で読み上げます"


@dataclasses.dataclass
class GuildTtsState:
    text_channel_id: int
    voice_client: discord.VoiceClient
    notify_join_leave: bool = False
    queue: asyncio.Queue = dataclasses.field(default_factory=asyncio.Queue)
    worker_task: asyncio.Task = None


class TtsManager:
    def __init__(self, engine=None, store=None):
        self.engine = engine or VoicevoxEngine()
        self.store = store or UserVoiceStore()
        # ギルド単位の読み上げ状態
        self.guild_states = {}
        # 発言者 (user_id) ごとの話者 ID。永続化済みの設定を読み込んでおく
        self.user_speakers = self.store.load_all_speakers()

    def is_reading(self, guild_id):
        return guild_id in self.guild_states

    def is_target_channel(self, guild_id, channel_id):
        state = self.guild_states.get(guild_id)
        return state is not None and state.text_channel_id == channel_id

    def should_notify(self, guild_id):
        state = self.guild_states.get(guild_id)
        return state is not None and state.notify_join_leave

    # 話者設定をメモリと DB の両方へ反映し、再起動後も残るようにする
    def set_speaker(self, user_id, speaker_id, display_name=None):
        self.user_speakers[user_id] = speaker_id
        self.store.set_speaker(user_id, speaker_id, display_name)

    def get_speaker(self, user_id):
        # 未設定の発言者は既定話者で読み上げる
        return self.user_speakers.get(user_id, self.engine.default_speaker)

    # 声設定 UI の試聴用に、固定サンプル文を指定話者で合成して WAV バイト列を返す。
    # VC 接続を伴わないので読み上げ中でなくても呼べる
    async def synthesize_sample(self, speaker):
        return await self.engine.synthesize_speech(SAMPLE_VOICE_TEXT, speaker)

    # 読み上げを開始し、ギルド用のワーカータスクを起動する
    def start_reading(self, guild_id, text_channel_id, voice_client, notify_join_leave):
        state = GuildTtsState(
            text_channel_id=text_channel_id,
            voice_client=voice_client,
            notify_join_leave=notify_join_leave,
        )
        state.worker_task = asyncio.create_task(self._run_worker(state))
        self.guild_states[guild_id] = state

    # 読み上げを終了し、ワーカーを止めて VC から切断する
    async def stop_reading(self, guild_id):
        state = self.guild_states.pop(guild_id, None)
        if state is None:
            return
        if state.worker_task is not None:
            state.worker_task.cancel()
        if state.voice_client is not None and state.voice_client.is_connected():
            await state.voice_client.disconnect()

    # 発言者名を先頭に付け、発言者の話者でテキストを読み上げキューへ積む
    def enqueue_message(self, guild_id, text, user_id, display_name):
        body = sanitize_text(text)
        # サニタイズ後に本文が空なら、名前だけ読み上げても無意味なので積まない
        if not body:
            return
        reading = f"{display_name}、{body}"
        self._put(guild_id, reading, self.get_speaker(user_id))

    # 入退室通知など、既定話者で読み上げるテキストを積む
    def enqueue_notice(self, guild_id, text):
        body = sanitize_text(text)
        if not body:
            return
        self._put(guild_id, body, self.engine.default_speaker)

    def _put(self, guild_id, text, speaker):
        state = self.guild_states.get(guild_id)
        if state is None:
            return
        state.queue.put_nowait((text, speaker))

    async def _run_worker(self, state):
        while True:
            text, speaker = await state.queue.get()
            try:
                await self._play_one(state, text, speaker)
            except Exception as e:
                # エラーは握りつぶさずログに残し、次の発言へ進む
                print(f"❌ TTS playback failed: {e}")
            finally:
                state.queue.task_done()

    # 1 件分の音声を合成して再生し、再生完了まで待つ
    async def _play_one(self, state, text, speaker):
        wav = await self.engine.synthesize_speech(text, speaker)
        # Opus エンコードを ffmpeg 側に任せ、Python 側の libopus ロードを不要にする
        source = discord.FFmpegOpusAudio(io.BytesIO(wav), pipe=True)
        loop = asyncio.get_running_loop()
        done = asyncio.Event()
        state.voice_client.play(
            source,
            after=lambda e: loop.call_soon_threadsafe(done.set),
        )
        await done.wait()
