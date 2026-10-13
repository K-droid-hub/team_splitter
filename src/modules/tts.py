import dataclasses
import os

import discord
from discord import app_commands
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

# スラッシュコマンドの選択肢は一覧から生成し、パネルと順番・表示名を揃える
SPEAKER_CHOICES = [
    app_commands.Choice(name=speaker.name, value=speaker.id) for speaker in SPEAKERS
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

    guild_ids_raw = os.getenv("GUILD_IDS", "")
    GUILD_IDS = [int(gid.strip()) for gid in guild_ids_raw.split(",") if gid]

    @app_commands.command(name="読み上げ開始")
    @app_commands.guilds(*[discord.Object(id=guild_id) for guild_id in GUILD_IDS])
    @app_commands.describe(入退室通知="入退室を読み上げるか (default: しない)")
    async def start_reading(self, interaction: discord.Interaction, 入退室通知: bool = False):
        if interaction.user.voice is None:
            await interaction.response.send_message(
                "ボイスチャンネルに入ってから実行してください。", ephemeral=True
            )
            return
        if self.tts_manager.is_target_channel(interaction.guild_id, interaction.channel_id):
            await interaction.response.send_message(
                "すでにこのチャンネルで読み上げ中です。", ephemeral=True
            )
            return
        # VC 接続に時間がかかっても 3 秒制限で落ちないよう応答を保留する
        await interaction.response.defer()
        error = await begin_reading(
            self.bot, interaction.channel_id, interaction.user.voice.channel, 入退室通知
        )
        if error is not None:
            await interaction.followup.send(error, ephemeral=True)
            return
        embed = discord.Embed(color=discord.Color.green())
        embed.add_field(
            name="読み上げ開始",
            value="このチャンネルの発言を読み上げます。",
            inline=False,
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="読み上げ終了")
    @app_commands.guilds(*[discord.Object(id=guild_id) for guild_id in GUILD_IDS])
    async def stop_reading(self, interaction: discord.Interaction):
        text_channel_id = self._reading_target(interaction)
        if text_channel_id is None:
            await interaction.response.send_message(
                "読み上げは開始されていません。", ephemeral=True
            )
            return
        await self.tts_manager.stop_reading(text_channel_id)
        embed = discord.Embed(color=discord.Color.green())
        embed.add_field(name="読み上げ終了", value="読み上げを終了しました。", inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="読み上げ声設定")
    @app_commands.guilds(*[discord.Object(id=guild_id) for guild_id in GUILD_IDS])
    @app_commands.describe(話者="あなたの発言を読み上げる声")
    @app_commands.choices(話者=SPEAKER_CHOICES)
    async def set_voice(self, interaction: discord.Interaction, 話者: app_commands.Choice[int]):
        self.tts_manager.set_speaker(
            interaction.user.id, 話者.value, interaction.user.display_name
        )
        # 名前だけでは声質が分からない話者があるので、確認にも説明を添える
        speaker = find_speaker(話者.value)
        detail = f"({speaker.description})" if speaker is not None else ""
        embed = discord.Embed(color=discord.Color.green())
        embed.add_field(
            name="読み上げ声設定",
            value=f"あなたの声を「{話者.name}」{detail}に設定しました。",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # 終了する読み上げを決める。このチャンネルが読み上げ対象ならそれを、
    # そうでなければ実行者のいるボイスチャンネルの読み上げを対象にする
    def _reading_target(self, interaction):
        if self.tts_manager.is_target_channel(interaction.guild_id, interaction.channel_id):
            return interaction.channel_id
        voice = interaction.user.voice
        if voice is None:
            return None
        state = self.tts_manager.state_for_voice(voice.channel.id)
        return None if state is None else state.text_channel_id

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
            if state is not None and state.notify_join_leave:
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
