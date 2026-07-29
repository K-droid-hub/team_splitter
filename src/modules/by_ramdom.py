import discord, os
from discord import app_commands
from discord.ext import commands

class ByRandom(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.split_team = bot.split_team # メインのインスタンスを共有

    guild_ids_raw = os.getenv("GUILD_IDS", "")
    GUILD_IDS = [int(gid.strip()) for gid in guild_ids_raw.split(",") if gid]

    @app_commands.command(name="人数分抽選")
    @app_commands.guilds(*[discord.Object(id=guild_id) for guild_id in GUILD_IDS])

    @app_commands.describe(人数 = "任意(default: 5)", 優先者 = "任意(例: user1,user2)")

    async def by_random(self, interaction: discord.Interaction, 人数: int = 5, 優先者: str = ""):
        priorities = [m.strip() for m in 優先者.split(',') if m.strip()] # もし優先メンバーがあればリストに追加
        if priorities:
            for priority in priorities:
                self.split_team.add_special(interaction, addName=priority, isViewer=False, isPriority=True, isRocked=False)
        result = self.split_team.split(interaction, teamCount=1, teamSize=人数)
        embed = discord.Embed(color=discord.Color.purple())
        embed.add_field(name="人数分抽選", value=result, inline=False)
        await interaction.response.send_message(embed=embed)

# Cog 登録
async def setup(bot: commands.Bot):
    await bot.add_cog(ByRandom(bot))