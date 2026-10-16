import audioop
import collections
import io
import queue
import threading
import wave

import discord

# discord.py が 1 回の送信で求める PCM の長さ(20ms)。48kHz・ステレオ・16bit
FRAME_SIZE = discord.opus.Encoder.FRAME_SIZE
SAMPLE_RATE = discord.opus.Encoder.SAMPLING_RATE
CHANNELS = discord.opus.Encoder.CHANNELS
SAMPLE_WIDTH = 2
SILENCE = b"\x00" * FRAME_SIZE

# 読み上げ中に音楽を下げる倍率。0.3 でおよそ -10dB
DUCKING_GAIN = 0.3
# 倍率を 1 フレームで動かす幅。一気に切り替えるとプツッと鳴るので数フレームかけて動かす
GAIN_STEP = 0.1
# 音楽の先読みフレーム数(50 = 1 秒)。回線の一時的な詰まりはこの長さまで吸収する
MUSIC_BUFFER_FRAMES = 50


# VOICEVOX の WAV(既定は 24kHz・モノラル)を discord.py が送れる形式の PCM にそろえる
def wav_to_pcm(wav_bytes):
    with wave.open(io.BytesIO(wav_bytes)) as wav:
        width = wav.getsampwidth()
        channels = wav.getnchannels()
        rate = wav.getframerate()
        pcm = wav.readframes(wav.getnframes())
    if width != SAMPLE_WIDTH:
        pcm = audioop.lin2lin(pcm, width, SAMPLE_WIDTH)
    if rate != SAMPLE_RATE:
        pcm, _ = audioop.ratecv(pcm, SAMPLE_WIDTH, channels, rate, SAMPLE_RATE, None)
    if channels == 1:
        pcm = audioop.tostereo(pcm, SAMPLE_WIDTH, 1, 1)
    return pcm


class MusicStream:
    """ffmpeg の PCM 出力を別スレッドで先読みするバッファ。

    ミキサーの read() は送信スレッドで 20ms ごとに呼ばれ、待たせると読み上げまで
    止まってしまう。そこで ffmpeg からの読み込みは専用スレッドに任せ、ミキサーは
    溜まった分を待たずに取り出すだけにする
    """

    def __init__(self, source):
        self.source = source
        self.frames = queue.Queue(maxsize=MUSIC_BUFFER_FRAMES)
        self.eof = False
        self._stopped = threading.Event()
        self._cleanup_lock = threading.Lock()
        self._cleaned = False
        self._thread = threading.Thread(target=self._pump, daemon=True)

    def start(self):
        self._thread.start()

    # 最後まで読み終え、かつ取り出し切ったら終わり
    @property
    def done(self):
        return self.eof and self.frames.empty()

    # 溜まっていなければ None を返す。回線が詰まっていても待たない
    def read_frame(self):
        try:
            return self.frames.get_nowait()
        except queue.Empty:
            return None

    def stop(self):
        self._stopped.set()
        # ffmpeg を止めると、読み込み待ちで固まっているスレッドも抜けられる
        self._cleanup()

    def _cleanup(self):
        with self._cleanup_lock:
            if self._cleaned:
                return
            self._cleaned = True
        try:
            self.source.cleanup()
        except Exception as e:
            print(f"⚠️ ffmpeg の後始末に失敗: {e}")

    def _pump(self):
        try:
            while not self._stopped.is_set():
                frame = self.source.read()
                if not frame:
                    break
                # 満杯なら空くまで待つ。止められたらすぐ抜けられるよう短い間隔で見直す
                while not self._stopped.is_set():
                    try:
                        self.frames.put(frame, timeout=0.1)
                        break
                    except queue.Full:
                        continue
        except Exception as e:
            # 停止で ffmpeg を落としたときにも読み込みが失敗するので、停止中は黙る
            if not self._stopped.is_set():
                print(f"❌ 音楽の読み込みに失敗: {e}")
        finally:
            self.eof = True
            self._cleanup()


class MixedAudioSource(discord.AudioSource):
    """読み上げと音楽を 1 本に重ねて流す音源。

    ボイス接続 1 本で同時に鳴らせる音源は 1 つだけなので、2 系統をここで合成する。
    読み上げが鳴っている間は音楽を下げ、言葉が埋もれないようにする
    """

    def __init__(self):
        # 送信スレッドとイベントループの両方から触るので、状態の出し入れは必ず鍵を取る
        self._lock = threading.Lock()
        # 読み上げの PCM を [残りのバイト列, 位置, 再生し終えたときの通知] で並べる
        self._clips = collections.deque()
        self._music = None
        self._music_done = None
        self._gain = 1.0
        # ユーザーが指定した音楽の音量(0.0〜1.0)。読み上げによる自動の下げとは掛け合わせる
        self._music_volume = 1.0

    def is_opus(self):
        return False

    # 再生スレッドは止まるたびにこれを呼ぶ。無音で一時停止しただけで中身が消えないよう、
    # ここでは何もしない。後始末は close() で行う
    def cleanup(self):
        pass

    def has_pending(self):
        with self._lock:
            return bool(self._clips) or self._music is not None

    def add_clip(self, pcm, on_done):
        with self._lock:
            self._clips.append([pcm, 0, on_done])

    def set_music(self, stream, on_done):
        with self._lock:
            previous, previous_done = self._music, self._music_done
            self._music, self._music_done = stream, on_done
        self._finish_music(previous, previous_done)
        stream.start()

    # 再生中の曲を止める。曲の終わりを待っている側にも完了を伝える
    def stop_music(self):
        with self._lock:
            stream, on_done = self._music, self._music_done
            self._music = self._music_done = None
        self._finish_music(stream, on_done)

    # 音楽の音量を 0〜100 で設定する。範囲外は端に丸める
    def set_music_volume(self, percent):
        with self._lock:
            self._music_volume = max(0, min(100, percent)) / 100

    def music_volume(self):
        with self._lock:
            return self._music_volume * 100

    # 読み上げを取り消す。待っている側を起こすため、未再生の分にも完了を伝える
    def clear_clips(self):
        with self._lock:
            pending = [clip[2] for clip in self._clips]
            self._clips.clear()
        for on_done in pending:
            on_done()

    def close(self):
        self.stop_music()
        self.clear_clips()

    def _finish_music(self, stream, on_done):
        if stream is not None:
            stream.stop()
        if on_done is not None:
            on_done()

    def read(self):
        finished = []
        with self._lock:
            voice = self._read_clip(finished)
            music = self._read_music(finished)
            idle = voice is None and not self._clips and self._music is None
        # 通知は鍵の外で出す。受け取り側が何をしても送信を詰まらせないため
        for on_done in finished:
            on_done()
        if idle:
            # 何も流すものがないので、空を返して送信スレッドを止める。
            # 次の音が来たら呼び出し側がもう一度再生を始める
            return b""
        return self._mix(voice, music)

    def _read_clip(self, finished):
        if not self._clips:
            return None
        clip = self._clips[0]
        pcm, offset, on_done = clip
        frame = pcm[offset : offset + FRAME_SIZE]
        clip[1] = offset + FRAME_SIZE
        if clip[1] >= len(pcm):
            self._clips.popleft()
            finished.append(on_done)
        if len(frame) < FRAME_SIZE:
            frame += b"\x00" * (FRAME_SIZE - len(frame))
        return frame

    def _read_music(self, finished):
        if self._music is None:
            return None
        frame = self._music.read_frame()
        if frame is None and self._music.done:
            finished.append(self._music_done)
            self._music = self._music_done = None
            return None
        # 先読みが追いついていないだけなら、曲は続いているので無音で埋める
        return SILENCE if frame is None else frame

    def _mix(self, voice, music):
        target = DUCKING_GAIN if voice is not None else 1.0
        if self._gain < target:
            self._gain = min(target, self._gain + GAIN_STEP)
        elif self._gain > target:
            self._gain = max(target, self._gain - GAIN_STEP)
        if music is None:
            return voice if voice is not None else SILENCE
        level = self._gain * self._music_volume
        if level != 1.0:
            music = audioop.mul(music, SAMPLE_WIDTH, level)
        if voice is None:
            return music
        # 足し合わせて範囲を超えた分は audioop が頭打ちにしてくれる
        return audioop.add(voice, music, SAMPLE_WIDTH)
