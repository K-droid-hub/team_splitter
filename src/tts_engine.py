import os
import re

import aiohttp

# 読み上げの最大文字数。超えた分は打ち切る
MAX_READ_LENGTH = 100
# URL・カスタム絵文字・メンションを検出する正規表現
URL_PATTERN = re.compile(r"https?://\S+")
CUSTOM_EMOJI_PATTERN = re.compile(r"<a?:\w+:\d+>")
MENTION_PATTERN = re.compile(r"<@[!&]?\d+>|<#\d+>")
# 読み上げの音量。VOICEVOX の volumeScale に渡す(既定は 1.0)。音楽より小さく聞こえるため上げている
VOICE_VOLUME_SCALE = 2.0


def sanitize_text(text):
    # URL は長く読み上げても無意味なため短い語に置換する
    text = URL_PATTERN.sub("URL 省略", text)
    # カスタム絵文字とメンションは ID の羅列になるため除去する
    text = CUSTOM_EMOJI_PATTERN.sub("", text)
    text = MENTION_PATTERN.sub("", text)
    text = text.strip()
    if len(text) > MAX_READ_LENGTH:
        text = text[:MAX_READ_LENGTH] + " 以下省略"
    return text


class VoicevoxEngine:
    def __init__(self, base_url=None, default_speaker=None):
        self.base_url = (base_url or os.getenv("VOICEVOX_URL", "http://localhost:50021")).rstrip("/")
        self.default_speaker = int(default_speaker or os.getenv("VOICEVOX_SPEAKER", "3"))

    # 話者のモデルを先に読み込んでおく。初回の合成が遅くなるのを避けるため VC 参加時に呼ぶ。
    # skip_reinit で読み込み済みなら何もしない
    async def initialize_speaker(self, speaker):
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/initialize_speaker",
                params={"speaker": speaker, "skip_reinit": "true"},
            ) as res:
                res.raise_for_status()

    # VOICEVOX の 2 段 API (audio_query -> synthesis) で wav バイト列を生成する
    async def synthesize_speech(self, text, speaker=None):
        speaker = self.default_speaker if speaker is None else speaker
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/audio_query",
                params={"text": text, "speaker": speaker},
            ) as query_res:
                query_res.raise_for_status()
                query = await query_res.json()
            query["volumeScale"] = VOICE_VOLUME_SCALE
            async with session.post(
                f"{self.base_url}/synthesis",
                params={"speaker": speaker},
                json=query,
            ) as synthesis_res:
                synthesis_res.raise_for_status()
                return await synthesis_res.read()
