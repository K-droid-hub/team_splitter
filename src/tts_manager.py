import asyncio
import dataclasses

from src.audio_mixer import wav_to_pcm
from src.tts_engine import VoicevoxEngine, sanitize_text
from src.user_voice_store import UserVoiceStore
from src.voice_session import USE_TTS, VoiceSession

# 声設定 UI の試聴で読み上げる固定サンプル文
SAMPLE_VOICE_TEXT = "こんにちは、この声で読み上げます"


@dataclasses.dataclass
class TtsState:
    guild_id: int
    text_channel_id: int
    session: VoiceSession
    notify_join_leave: bool = False
    queue: asyncio.Queue = dataclasses.field(default_factory=asyncio.Queue)
    worker_task: asyncio.Task = None


class TtsManager:
    def __init__(self, engine=None, store=None):
        self.engine = engine or VoicevoxEngine()
        self.store = store or UserVoiceStore()
        # 読み上げ対象のテキストチャンネルごとの状態。同じサーバーでも
        # ボイスチャンネルが違えば、別々のチャンネルの発言を同時に読み上げられる
        self.states = {}
        # 読み上げ対象のテキストチャンネルごとの入退室通知の設定。停止後も次回の開始で使う
        self.join_leave_prefs = {}
        # 発言者 (user_id) ごとの話者 ID。永続化済みの設定を読み込んでおく
        self.user_speakers = self.store.load_all_speakers()
        # 話者の読み込みを裏で走らせるタスク。参照を持っておかないと途中で回収されてしまう
        self._warm_tasks = set()

    def is_reading(self, guild_id):
        return any(state.guild_id == guild_id for state in self.states.values())

    def is_target_channel(self, guild_id, channel_id):
        state = self.states.get(channel_id)
        return state is not None and state.guild_id == guild_id

    # そのボイスチャンネルで読み上げている状態を返す。入退室通知や自動退出に使う
    def state_for_voice(self, voice_channel_id):
        for state in self.states.values():
            if state.session.is_alive() and state.session.channel_id == voice_channel_id:
                return state
        return None

    # 話者設定をメモリと DB の両方へ反映し、再起動後も残るようにする
    def set_speaker(self, user_id, speaker_id, display_name=None):
        self.user_speakers[user_id] = speaker_id
        self.store.set_speaker(user_id, speaker_id, display_name)

    def get_speaker(self, user_id):
        # 未設定の発言者は既定話者で読み上げる
        return self.user_speakers.get(user_id, self.engine.default_speaker)

    # 自分で選んだ声か、既定の声のままかを声設定 UI で出し分けるために使う
    def has_speaker(self, user_id):
        return user_id in self.user_speakers

    # 声設定 UI の試聴用に、固定サンプル文を指定話者で合成して WAV バイト列を返す。
    # VC 接続を伴わないので読み上げ中でなくても呼べる
    async def synthesize_sample(self, speaker):
        return await self.engine.synthesize_speech(SAMPLE_VOICE_TEXT, speaker)

    # 話者のモデルを VOICEVOX に裏で読み込ませる。VC 参加や読み上げ開始の応答を待たせないよう
    # タスクにする。失敗しても読み上げは通常どおり動くので、ログだけ残す
    def warm_speakers(self, speaker_ids):
        for speaker_id in set(speaker_ids):
            task = asyncio.create_task(self._warm_speaker(speaker_id))
            self._warm_tasks.add(task)
            task.add_done_callback(self._warm_tasks.discard)

    async def _warm_speaker(self, speaker_id):
        try:
            await self.engine.initialize_speaker(speaker_id)
        except Exception as e:
            print(f"❌ 話者の読み込みに失敗: {e}")

    # 読み上げを開始し、チャンネル用のワーカータスクを起動する。
    # session は呼び出し側が USE_TTS で確保済みのものを渡す
    def start_reading(self, text_channel_id, session, notify_join_leave):
        self.join_leave_prefs[text_channel_id] = notify_join_leave
        state = TtsState(
            guild_id=session.guild_id,
            text_channel_id=text_channel_id,
            session=session,
            notify_join_leave=notify_join_leave,
        )
        state.worker_task = asyncio.create_task(self._run_worker(state))
        self.states[text_channel_id] = state

    # パネルの入退室通知ボタン用。読み上げ中なら即反映し、次回の開始時も同じ設定を使う
    def set_join_leave_notice(self, text_channel_id, enabled):
        self.join_leave_prefs[text_channel_id] = enabled
        state = self.states.get(text_channel_id)
        if state is not None:
            state.notify_join_leave = enabled

    def join_leave_notice(self, text_channel_id):
        return self.join_leave_prefs.get(text_channel_id, False)

    # 読み上げを終了する。音楽も使っていなければ、そこで接続も切れる
    async def stop_reading(self, text_channel_id):
        state = self.states.pop(text_channel_id, None)
        if state is None:
            return
        if state.worker_task is not None:
            state.worker_task.cancel()
        state.session.clear_clips()
        await state.session.release(USE_TTS)

    # 発言者名を先頭に付け、発言者の話者でテキストを読み上げキューへ積む
    def enqueue_message(self, text_channel_id, text, user_id, display_name):
        body = sanitize_text(text)
        # サニタイズ後に本文が空なら、名前だけ読み上げても無意味なので積まない
        if not body:
            return
        reading = f"{display_name}、{body}"
        self._put(text_channel_id, reading, self.get_speaker(user_id))

    # 入退室通知など、既定話者で読み上げるテキストを積む
    def enqueue_notice(self, text_channel_id, text):
        body = sanitize_text(text)
        if not body:
            return
        self._put(text_channel_id, body, self.engine.default_speaker)

    def _put(self, text_channel_id, text, speaker):
        state = self.states.get(text_channel_id)
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

    # 1 件分の音声を合成してミキサーへ流し、鳴り終わるまで待つ。
    # 音楽と同じ接続で重ねて鳴らすため、ffmpeg を通さず PCM にそろえて渡す
    async def _play_one(self, state, text, speaker):
        wav = await self.engine.synthesize_speech(text, speaker)
        await state.session.play_clip(wav_to_pcm(wav))
