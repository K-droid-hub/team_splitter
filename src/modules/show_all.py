import discord
from discord.ext import commands
from discord import app_commands
from src.config import GUILD_IDS

class ShowAll(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.split_team = bot.split_team # メインのインスタンスを共有

    @app_commands.command(name="一覧表示")
    @app_commands.guilds(*[discord.Object(id=gid) for gid in GUILD_IDS])

    async def show_command(self, interaction: discord.Interaction):
        self.split_team.set_member(interaction)
        members = self.split_team.channel_members
        # 各状態のリストを取得
        rockedViewers   = ", ".join(m.name for m in members if m.viewer and m.locked)           or "なし"
        viewers         = ", ".join(m.name for m in members if m.viewer and not m.locked)       or "なし"
        rockedPriorities= ", ".join(m.name for m in members if m.priority and m.locked)         or "なし"
        priorities      = ", ".join(m.name for m in members if m.priority and not m.locked)     or "なし"
        channel_members = ", ".join(m.name for m in members if not m.viewer and not m.priority) or "なし"
        embed = discord.Embed(title="一覧表示", color=discord.Color.red())
        embed.add_field(name="チャンネルメンバー", value=channel_members, inline=False)
        embed.add_field(name="固定観戦者", value=rockedViewers, inline=False)
        embed.add_field(name="次だけ観戦者", value=viewers, inline=False)
        embed.add_field(name="固定優先者", value=rockedPriorities, inline=False)
        embed.add_field(name="次だけ優先者", value=priorities, inline=False)
        await interaction.response.send_message(embed=embed)

# Cog 登録
async def setup(bot: commands.Bot):
    await bot.add_cog(ShowAll(bot))