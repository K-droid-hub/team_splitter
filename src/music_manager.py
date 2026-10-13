import asyncio
import collections
import dataclasses

import discord

from src import music_source
from src.voice_session import USE_MUSIC, VoiceSession

# キューが空になってから音楽をやめるまでの秒数。次の曲を足す間を残しつつ、
# 放置でボイス接続の枠が埋まり続けるのを防ぐ
IDLE_TIMEOUT_SECONDS = 60
# 1 つのボイスチャンネルのキューに積める上限
MAX_QUEUE_SIZE = 100


@dataclasses.dataclass
class MusicState:
    session: VoiceSession
    text_channel: object
    queue: collections.deque = dataclasses.field(default_factory=collections.deque)
    # 空キューで待っているワーカーを起こすための合図
    added: asyncio.Event = dataclasses.field(default_factory=asyncio.Event)
    worker_task: asyncio.Task = None
    current: music_source.Track = None


class MusicManager:
    """ボイスチャンネルごとの再生キューを持ち、順番にミキサーへ流す"""

    def __init__(self, source=None):
        # テストから差し替えられるよう、yt-dlp 呼び出しは注入可能にしておく
        self.source = source or music_source
        # 接続ごとの状態。bot がドラッグで移されても追えるよう接続そのものをキーにする
        self.states = {}

    def state_for_session(self, session):
        return self.states.get(session)

    def state_for_voice(self, voice_channel_id):
        for state in self.states.values():
            if state.session.is_alive() and state.session.channel_id == voice_channel_id:
                return state
        return None

    def states_in_guild(self, guild_id):
        return [
            s for s in self.states.values()
            if s.session.is_alive() and s.session.guild_id == guild_id
        ]

    # 再生を開始し、接続用のワーカータスクを起動する。
    # session は呼び出し側が USE_MUSIC で確保済みのものを渡す
    def start(self, session, text_channel):
        state = MusicState(session=session, text_channel=text_channel)
        state.worker_task = asyncio.create_task(self._run_worker(state))
        self.states[session] = state
        return state

    # キューへ積み、実際に積めた数を返す。上限超過分は呼び出し側から伝えられる
    def enqueue(self, state, tracks):
        space = MAX_QUEUE_SIZE - len(state.queue)
        accepted = list(tracks)[: max(space, 0)]
        state.queue.extend(accepted)
        if accepted:
            state.added.set()
        return len(accepted)

    # 再生中の曲を止める。ワーカーは曲の終わりとして受け取り、次の曲へ進む
    def skip(self, state):
        if state.current is None:
            return False
        state.session.skip_track()
        return True

    # 音楽を終了する。読み上げも使っていなければ、そこで接続も切れる
    async def stop(self, state):
        if self.states.get(state.session) is not state:
            return
        self._forget(state)
        state.queue.clear()
        if state.worker_task is not None:
            state.worker_task.cancel()
        state.session.skip_track()
        await state.session.release(USE_MUSIC)

    def _forget(self, state):
        if self.states.get(state.session) is state:
            del self.states[state.session]

    async def _notify(self, state, text):
        if state.text_channel is None:
            return
        try:
            await state.text_channel.send(text)
        except Exception as e:
            # 通知の失敗で再生を止めたくないので、ログだけ残して続行する
            print(f"⚠️ 音楽の通知送信に失敗: {e}")

    async def _run_worker(self, state):
        while True:
            if not state.queue:
                state.added.clear()
                try:
                    await asyncio.wait_for(state.added.wait(), IDLE_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    # 時間切れと同時に曲が積まれていたら、終わらずに続ける
                    if state.queue:
                        continue
                    # stop() を呼ぶと自タスクをキャンセルすることになるので、
                    # ここで直接後始末する
                    self._forget(state)
                    await self._notify(state, "キューが空になったので音楽を終了します。")
                    await state.session.release(USE_MUSIC)
                    return
                continue
            track = state.queue.popleft()
            state.current = track
            try:
                await self._play_one(state, track)
            except Exception as e:
                # 1 曲の失敗で止めず、理由を伝えて次へ進む
                print(f"❌ 音楽の再生に失敗: {e}")
                await self._notify(state, f"「{track.title}」の再生に失敗しました。")
            finally:
                state.current = None

    # 1 曲分のストリームをミキサーへ流し、曲の終わりかスキップまで待つ
    async def _play_one(self, state, track):
        stream_url = await self.source.resolve_stream_url(track)
        # 読み上げと重ねるためミキサーで合成するので、ffmpeg には PCM を出させる
        source = discord.FFmpegPCMAudio(
            stream_url,
            before_options=music_source.FFMPEG_BEFORE_OPTIONS,
            options=music_source.FFMPEG_OPTIONS,
        )
        length = music_source.format_duration(track.duration)
        await self._notify(state, f"▶️ 再生中: {track.title} [{length}]")
        await state.session.play_track(source)
