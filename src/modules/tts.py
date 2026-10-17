import dataclasses

import discord
from discord.ext import commands

from src.voice_pool import VoiceSlotUnavailable
from src.voice_session import USE_TTS

# 読み上げ声の一覧。name は UI 表示名、description は名前だけでは声質が分からない
# 話者のための短い手がかり。id は VOICEVOX のスタイル ID で、青山龍星は同一キャラの
# 声色違い((1)=熱血・(2)=喜び)なので通し番号で区別する
@dataclasses.dataclass(frozen=True)
class Speaker:
    id: int
    name: str
    description: str


SPEAKERS = [
    Speaker(2, "四国めたん", "落ち着いたトーンのはっきりした少女声"),
    Speaker(3, "ずんだもん", "明るく元気な中性的な子供声"),
    Speaker(8, "春日部つむぎ", "軽やかで明るい女子高生の声"),
    Speaker(9, "波音リツ", "低めでクールな女性声"),
    Speaker(10, "雨晴はう", "やわらかく親しみのある女性声"),
    Speaker(42, "ちび式じい", "しゃがれた声の小さなおじいさん"),
    Speaker(81, "青山龍星(1)", "低く力のこもった男性声"),
    Speaker(83, "青山龍星(2)", "低めで明るく弾んだ男性声"),
    Speaker(79, "もち子さん", "やわらかく明るい女性声"),
    Speaker(89, "Voidoll", "無機質で機械的な声"),
]

_SPEAKERS_BY_ID = {speaker.id: speaker for speaker in SPEAKERS}


def find_speaker(speaker_id):
    # 一覧外のスタイル ID(VOICEVOX_SPEAKER に任意の値を指定した場合)では None を返す
    return _SPEAKERS_BY_ID.get(speaker_id)


# /読み上げ開始 とパネルのボタンで共通の開始処理。応答を保留(defer)してから呼ぶ。
# 失敗したときは利用者向けの文言を、成功したときは None を返す
async def begin_reading(bot, text_channel_id, voice_channel, notify_join_leave):
    manager = bot.tts_manager
    # 1 つのボイスチャンネルで読むテキストチャンネルは 1 つまで。混ざると誰の発言か分からない
    other = manager.state_for_voice(voice_channel.id)
    if other is not None:
        return f"このボイスチャンネルでは <#{other.text_channel_id}> の発言を読み上げ中です。"
    # 接続を待つ間に、いまVCにいる人の話者も含めて読み込んでおく。初回の発言を遅らせないため
    manager.warm_speakers(
        [manager.engine.default_speaker]
        + [manager.get_speaker(m.id) for m in voice_channel.members if not m.bot]
    )
    try:
        session = await bot.voice_pool.acquire(voice_channel, USE_TTS)
    except VoiceSlotUnavailable as e:
        return str(e)
    except Exception as e:
        # 接続失敗を握りつぶさず、原因の手掛かりとともに通知する
        print(f"❌ VC 接続に失敗: {e}")
        return (
            "ボイスチャンネルへの接続に失敗しました。"
            "PyNaCl・ffmpeg・libopus が導入されているか確認してください。"
        )
    # 接続を待つ間に、別のテキストチャンネルから同じボイスチャンネルで始められていたら譲る。
    # 相手が読み上げとして接続を使っているので、こちらからは手放さない
    other = manager.state_for_voice(voice_channel.id)
    if other is not None:
        return f"このボイスチャンネルでは <#{other.text_channel_id}> の発言を読み上げ中です。"
    manager.start_reading(text_channel_id, session, notify_join_leave)
    return None


class Tts(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tts_manager = bot.tts_manager # メインのインスタンスを共有

    def _is_command(self, content):
        prefix = self.bot.command_prefix
        # command_prefix は文字列と複数指定のどちらも取り得る。
        # 関数を渡す形には対応しないので、その場合は読み上げ対象のままにする
        if isinstance(prefix, str):
            prefixes = (prefix,)
        elif isinstance(prefix, (tuple, list)):
            prefixes = tuple(prefix)
        else:
            return False
        return content.startswith(prefixes)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        if not self.tts_manager.is_target_channel(message.guild.id, message.channel.id):
            return
        # !play などのコマンドは読み上げても意味がないので飛ばす
        if self._is_command(message.content):
            return
        self.tts_manager.enqueue_message(
            message.channel.id,
            message.content,
            message.author.id,
            message.author.display_name,
        )

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # bot 自身の状態変化は無視する
        if member.bot:
            return
        before_id = before.channel.id if before.channel is not None else None
        after_id = after.channel.id if after.channel is not None else None
        # ミュート切り替えなど、チャンネルが変わらない変化は対象外
        if before_id == after_id:
            return
        # 読み上げはボイスチャンネルごとなので、入った先・出た元それぞれで通知する
        if after_id is not None:
            state = self.tts_manager.state_for_voice(after_id)
            if state is not None:
                # 途中から入ってきた人の話者も、最初の発言の前に読み込んでおく
                self.tts_manager.warm_speakers([self.tts_manager.get_speaker(member.id)])
                if state.notify_join_leave:
                    self.tts_manager.enqueue_notice(
                        state.text_channel_id, f"{member.display_name} が入室しました"
                    )
        if before_id is None:
            return
        state = self.tts_manager.state_for_voice(before_id)
        if state is None:
            return
        if state.notify_join_leave:
            self.tts_manager.enqueue_notice(
                state.text_channel_id, f"{member.display_name} が退室しました"
            )
        # bot 以外が誰もいなくなったら自動退出する。読み上げているのが 2 体目の bot でも、
        # メンバーはメイン bot 側から数えられる
        channel = self.bot.get_channel(before_id)
        if channel is None or any(not m.bot for m in channel.members):
            return
        await self.tts_manager.stop_reading(state.text_channel_id)
        # パネルのボタン表示を 読み上げ開始 に戻す
        panel_cog = self.bot.get_cog("PanelCog")
        if panel_cog is not None:
            await panel_cog.refresh_panels(member.guild)


# Cog 登録
async def setup(bot: commands.Bot):
    await bot.add_cog(Tts(bot))
