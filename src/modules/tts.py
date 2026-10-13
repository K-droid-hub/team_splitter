import dataclasses
import os

import discord
from discord import app_commands
from discord.ext import commands

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
        if self.tts_manager.is_reading(interaction.guild_id):
            await interaction.response.send_message(
                "すでに読み上げ中です。", ephemeral=True
            )
            return
        # VC 接続に時間がかかっても 3 秒制限で落ちないよう応答を保留する
        await interaction.response.defer()
        try:
            voice_client = await interaction.user.voice.channel.connect()
        except Exception as e:
            # 接続失敗を握りつぶさず、原因の手掛かりとともに通知する
            print(f"❌ VC 接続に失敗: {e}")
            await interaction.followup.send(
                "ボイスチャンネルへの接続に失敗しました。"
                "PyNaCl・ffmpeg が導入されているか確認してください。",
                ephemeral=True,
            )
            return
        self.tts_manager.start_reading(
            interaction.guild_id,
            interaction.channel_id,
            voice_client,
            入退室通知,
        )
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
        if not self.tts_manager.is_reading(interaction.guild_id):
            await interaction.response.send_message(
                "読み上げは開始されていません。", ephemeral=True
            )
            return
        await self.tts_manager.stop_reading(interaction.guild_id)
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

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        if not self.tts_manager.is_target_channel(message.guild.id, message.channel.id):
            return
        self.tts_manager.enqueue_message(
            message.guild.id,
            message.content,
            message.author.id,
            message.author.display_name,
        )

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        guild_id = member.guild.id
        if not self.tts_manager.is_reading(guild_id):
            return
        voice_client = member.guild.voice_client
        if voice_client is None:
            return
        bot_channel = voice_client.channel
        # bot 自身の状態変化は無視する
        if member.bot:
            return
        # 入退室通知
        if self.tts_manager.should_notify(guild_id):
            joined = before.channel != bot_channel and after.channel == bot_channel
            left = before.channel == bot_channel and after.channel != bot_channel
            if joined:
                self.tts_manager.enqueue_notice(guild_id, f"{member.display_name} が入室しました")
            elif left:
                self.tts_manager.enqueue_notice(guild_id, f"{member.display_name} が退室しました")
        # bot 以外が誰もいなくなったら自動退出する
        remaining = [m for m in bot_channel.members if not m.bot]
        if not remaining:
            await self.tts_manager.stop_reading(guild_id)
            # パネルのボタン表示を 読み上げ開始 に戻す
            panel_cog = self.bot.get_cog("PanelCog")
            if panel_cog is not None:
                await panel_cog.refresh_panels(member.guild)


# Cog 登録
async def setup(bot: commands.Bot):
    await bot.add_cog(Tts(bot))
