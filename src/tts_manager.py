import asyncio
import dataclasses
import io

import discord

from src.tts_engine import VoicevoxEngine, sanitize_text


@dataclasses.dataclass
class GuildTtsState:
    text_channel_id: int
    voice_client: discord.VoiceClient
    notify_join_leave: bool = False
    queue: asyncio.Queue = dataclasses.field(default_factory=asyncio.Queue)
    worker_task: asyncio.Task = None


class TtsManager:
    def __init__(self, engine=None):
        self.engine = engine or VoicevoxEngine()
        # ギルド単位の読み上げ状態
        self.guild_states = {}
        # 発言者 (user_id) ごとの話者 ID
        self.user_speakers = {}

    def is_reading(self, guild_id):
        return guild_id in self.guild_states

    def is_target_channel(self, guild_id, channel_id):
        state = self.guild_states.get(guild_id)
        return state is not None and state.text_channel_id == channel_id

    def should_notify(self, guild_id):
        state = self.guild_states.get(guild_id)
        return state is not None and state.notify_join_leave

    def set_speaker(self, user_id, speaker_id):
        self.user_speakers[user_id] = speaker_id

    def get_speaker(self, user_id):
        # 未設定の発言者は既定話者で読み上げる
        return self.user_speakers.get(user_id, self.engine.default_speaker)

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

    # 発言者の話者でテキストを読み上げキューへ積む
    def enqueue_message(self, guild_id, text, user_id):
        self._enqueue(guild_id, text, self.get_speaker(user_id))

    # 入退室通知など、既定話者で読み上げるテキストを積む
    def enqueue_notice(self, guild_id, text):
        self._enqueue(guild_id, text, self.engine.default_speaker)

    def _enqueue(self, guild_id, text, speaker):
        state = self.guild_states.get(guild_id)
        if state is None:
            return
        text = sanitize_text(text)
        # サニタイズ後に空になった発言は読み上げない
        if not text:
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
        source = discord.FFmpegPCMAudio(io.BytesIO(wav), pipe=True)
        loop = asyncio.get_running_loop()
        done = asyncio.Event()
        state.voice_client.play(
            source,
            after=lambda e: loop.call_soon_threadsafe(done.set),
        )
        await done.wait()
