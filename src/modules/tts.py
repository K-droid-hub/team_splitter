import os

import discord
from discord import app_commands
from discord.ext import commands

# 話者設定コマンドで提示する代表的な VOICEVOX 話者
SPEAKER_CHOICES = [
    app_commands.Choice(name="四国めたん", value=2),
    app_commands.Choice(name="ずんだもん", value=3),
    app_commands.Choice(name="春日部つむぎ", value=8),
    app_commands.Choice(name="波音リツ", value=9),
    app_commands.Choice(name="雨晴はう", value=10),
]


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
        self.tts_manager.set_speaker(interaction.user.id, 話者.value)
        embed = discord.Embed(color=discord.Color.green())
        embed.add_field(
            name="読み上げ声設定",
            value=f"あなたの声を「{話者.name}」に設定しました。",
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


# Cog 登録
async def setup(bot: commands.Bot):
    await bot.add_cog(Tts(bot))
