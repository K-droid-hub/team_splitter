import audioop
import io
import struct
import time
import wave

from src.audio_mixer import (
    DUCKING_GAIN,
    FRAME_SIZE,
    SILENCE,
    MixedAudioSource,
    MusicStream,
    wav_to_pcm,
)


def frame(value):
    # 全サンプルが同じ値の 1 フレーム。合成結果を振幅で確かめやすくする
    return struct.pack("<h", value) * (FRAME_SIZE // 2)


def peak(pcm):
    return audioop.max(pcm, 2)


class FakeStream:
    """MusicStream の代わり。先読み済みのフレームを順に返す"""

    def __init__(self, frames, eof=True):
        self.frames = list(frames)
        self.eof = eof
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    @property
    def done(self):
        return self.eof and not self.frames

    def read_frame(self):
        return self.frames.pop(0) if self.frames else None


class Recorder:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1


# ---- 何も無いとき ----

def test_idle_mixer_returns_empty_to_stop_sending():
    # 空を返すと送信スレッドが止まり、「発言中」表示も消える
    assert MixedAudioSource().read() == b""


def test_cleanup_keeps_pending_audio():
    # 送信スレッドは止まるたびに cleanup() を呼ぶ。そこで中身を捨ててはいけない
    mixer = MixedAudioSource()
    mixer.add_clip(frame(100), Recorder())
    mixer.cleanup()
    assert mixer.has_pending() is True


# ---- 読み上げ ----

def test_clip_is_padded_and_reports_completion():
    mixer = MixedAudioSource()
    done = Recorder()
    mixer.add_clip(frame(100) + frame(100)[: FRAME_SIZE // 2], done)
    first = mixer.read()
    assert done.calls == 0
    second = mixer.read()
    # 端数のフレームは無音で埋めて 20ms 分にそろえる
    assert len(second) == FRAME_SIZE
    assert second.endswith(b"\x00" * (FRAME_SIZE // 2))
    assert first == frame(100)
    assert done.calls == 1
    assert mixer.read() == b""


def test_clips_play_in_order():
    mixer = MixedAudioSource()
    mixer.add_clip(frame(1), Recorder())
    mixer.add_clip(frame(2), Recorder())
    assert mixer.read() == frame(1)
    assert mixer.read() == frame(2)


def test_clear_clips_wakes_every_waiter():
    mixer = MixedAudioSource()
    first, second = Recorder(), Recorder()
    mixer.add_clip(frame(1), first)
    mixer.add_clip(frame(2), second)
    mixer.clear_clips()
    assert (first.calls, second.calls) == (1, 1)
    assert mixer.read() == b""


# ---- 音楽 ----

def test_music_passes_through_without_voice():
    mixer = MixedAudioSource()
    stream = FakeStream([frame(1000)])
    mixer.set_music(stream, Recorder())
    assert stream.started is True
    assert mixer.read() == frame(1000)


def test_music_end_reports_completion():
    mixer = MixedAudioSource()
    done = Recorder()
    mixer.set_music(FakeStream([frame(1000)]), done)
    mixer.read()
    assert mixer.read() == b""
    assert done.calls == 1
    assert mixer.has_pending() is False


def test_stalled_music_sends_silence_without_stopping():
    # 回線が詰まって先読みが空でも、曲が終わったわけではないので送信は続ける
    mixer = MixedAudioSource()
    done = Recorder()
    mixer.set_music(FakeStream([], eof=False), done)
    assert mixer.read() == SILENCE
    assert done.calls == 0


def test_stalled_music_does_not_hold_back_voice():
    # 音楽が詰まっていても、読み上げはそのまま聞こえる
    mixer = MixedAudioSource()
    mixer.set_music(FakeStream([], eof=False), Recorder())
    mixer.add_clip(frame(500), Recorder())
    assert mixer.read() == frame(500)


def test_stop_music_stops_stream_and_wakes_waiter():
    mixer = MixedAudioSource()
    stream = FakeStream([frame(1)] * 10, eof=False)
    done = Recorder()
    mixer.set_music(stream, done)
    mixer.stop_music()
    assert stream.stopped is True
    assert done.calls == 1
    assert mixer.read() == b""


def test_set_music_replaces_previous_track():
    mixer = MixedAudioSource()
    old_stream, old_done = FakeStream([frame(1)], eof=False), Recorder()
    mixer.set_music(old_stream, old_done)
    mixer.set_music(FakeStream([frame(2)]), Recorder())
    assert old_stream.stopped is True
    assert old_done.calls == 1
    assert mixer.read() == frame(2)


# ---- 重ね合わせ ----

def test_voice_ducks_music_gradually():
    mixer = MixedAudioSource()
    mixer.set_music(FakeStream([frame(10000)] * 20, eof=False), Recorder())
    mixer.add_clip(frame(0) * 20, Recorder())
    peaks = [peak(mixer.read()) for _ in range(12)]
    # 一気に下げるとプツッと鳴るので、数フレームかけて下げる
    assert peaks[0] < 10000
    assert peaks[0] > peaks[3]
    assert peaks[-1] == int(10000 * DUCKING_GAIN)


def test_music_volume_recovers_after_voice():
    mixer = MixedAudioSource()
    mixer.set_music(FakeStream([frame(10000)] * 30, eof=False), Recorder())
    mixer.add_clip(frame(0) * 10, Recorder())
    for _ in range(10):
        mixer.read()
    peaks = [peak(mixer.read()) for _ in range(10)]
    assert peaks[0] < peaks[-1]
    assert peaks[-1] == 10000


def test_mixed_signal_is_clipped_instead_of_wrapping():
    # 足して範囲を超えたとき、符号が反転して爆音にならず頭打ちになること
    mixer = MixedAudioSource()
    mixer.set_music(FakeStream([frame(30000)], eof=False), Recorder())
    mixer.add_clip(frame(30000), Recorder())
    assert peak(mixer.read()) == 32767


# ---- MusicStream ----

class FakeSource:
    def __init__(self, frames, fail_after_cleanup=False):
        self.frames = list(frames)
        self.cleanups = 0
        self.fail_after_cleanup = fail_after_cleanup

    def read(self):
        if self.cleanups and self.fail_after_cleanup:
            raise AttributeError("stdout は既に閉じられています")
        return self.frames.pop(0) if self.frames else b""

    def cleanup(self):
        self.cleanups += 1


def wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.001)
    return False


def test_music_stream_prefetches_until_the_end():
    source = FakeSource([frame(1), frame(2)])
    stream = MusicStream(source)
    stream.start()
    assert wait_until(lambda: stream.eof)
    assert stream.read_frame() == frame(1)
    assert stream.read_frame() == frame(2)
    assert stream.done is True
    assert source.cleanups == 1


def test_music_stream_stop_cleans_up_once():
    source = FakeSource([frame(1)] * 1000, fail_after_cleanup=True)
    stream = MusicStream(source)
    stream.start()
    stream.stop()
    assert wait_until(lambda: stream.eof)
    # 停止後に読み込みが失敗しても落ちず、ffmpeg の後始末は 1 回だけ
    assert source.cleanups == 1


# ---- WAV の変換 ----

def make_wav(samples, rate, channels):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(struct.pack("<h", 1000) * samples * channels)
    return buffer.getvalue()


def test_voicevox_wav_is_converted_to_discord_format():
    # VOICEVOX 既定の 24kHz・モノラルを、48kHz・ステレオにそろえる
    pcm = wav_to_pcm(make_wav(samples=2400, rate=24000, channels=1))
    expected = 4800 * 2 * 2  # 0.1 秒 × 48kHz × ステレオ × 16bit
    assert abs(len(pcm) - expected) <= 8
    assert len(pcm) % 4 == 0


def test_wav_already_in_discord_format_is_unchanged():
    wav = make_wav(samples=960, rate=48000, channels=2)
    assert wav_to_pcm(wav) == struct.pack("<h", 1000) * 960 * 2
