import asyncio

from src.audio_mixer import MixedAudioSource, MusicStream

# 1 つの接続を使っている機能。両方が手放したら切断して枠を返す
USE_TTS = "tts"
USE_MUSIC = "music"
# 音楽の既定の音量(%)。目盛りで 50 に当たる。音楽だけが対象で、読み上げには掛からない
DEFAULT_MUSIC_PERCENT = 25


class VoiceSession:
    """bot 1 体が 1 つのボイスチャンネルに張った接続。

    読み上げと音楽は同じミキサーへ流し込み、1 本の接続で同時に鳴らす。
    鳴らすものが無い間は送信スレッドを止め、「発言中」の表示も消しておく
    """

    def __init__(self, voice_client, on_close=None, loop=None):
        self.voice_client = voice_client
        self.mixer = MixedAudioSource()
        self.mixer.set_music_volume(DEFAULT_MUSIC_PERCENT)
        self.users = set()
        self.closed = False
        self._on_close = on_close
        self._loop = loop or asyncio.get_running_loop()

    @property
    def guild_id(self):
        return self.voice_client.guild.id

    # bot がドラッグで別のチャンネルへ移されることもあるので、毎回いまの値を読む
    @property
    def channel(self):
        return self.voice_client.channel

    @property
    def channel_id(self):
        return self.voice_client.channel.id

    def is_alive(self):
        return not self.closed and self.voice_client.is_connected()

    def retain(self, user):
        self.users.add(user)

    # 使い終わった機能を外し、誰も使わなくなったら切断する
    async def release(self, user):
        self.users.discard(user)
        if self.users:
            return
        await self.close()

    async def close(self):
        if self.closed:
            return
        self.closed = True
        self.mixer.close()
        if self._on_close is not None:
            self._on_close(self)
        if self.voice_client.is_connected():
            await self.voice_client.disconnect()

    # 読み上げ 1 件分の PCM を流し、鳴り終わるまで待つ
    async def play_clip(self, pcm):
        if not self.is_alive():
            return
        done = asyncio.Event()
        self.mixer.add_clip(pcm, self._notifier(done))
        self._ensure_playing()
        await done.wait()

    # 1 曲を流し、曲の終わりかスキップまで待つ
    async def play_track(self, source):
        if not self.is_alive():
            source.cleanup()
            return
        done = asyncio.Event()
        self.mixer.set_music(MusicStream(source), self._notifier(done))
        self._ensure_playing()
        await done.wait()

    def skip_track(self):
        self.mixer.stop_music()

    def set_music_volume(self, percent):
        self.mixer.set_music_volume(percent)

    def music_volume(self):
        return self.mixer.music_volume()

    def clear_clips(self):
        self.mixer.clear_clips()

    # ミキサーの通知は送信スレッドから来るので、イベントループへ渡し直す
    def _notifier(self, event):
        return lambda: self._loop.call_soon_threadsafe(event.set)

    # 送信スレッドが止まっていて、流すものがあれば再生を始める
    def _ensure_playing(self):
        if not self.is_alive():
            # 切断済みなら鳴らしようがない。待っている側を起こして先へ進ませる
            self.mixer.close()
            return
        if self.voice_client.is_playing() or not self.mixer.has_pending():
            return
        try:
            self.voice_client.play(self.mixer, after=self._after)
        except Exception as e:
            print(f"❌ 音声の送信を開始できませんでした: {e}")
            self.mixer.close()

    def _after(self, error):
        if error is not None:
            print(f"❌ 音声の送信中にエラー: {error}")
        # 止まる直前に新しい音が積まれていたら、取りこぼさずもう一度鳴らす
        self._loop.call_soon_threadsafe(self._ensure_playing)
