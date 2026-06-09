import discord
from discord import app_commands
from discord.ext import commands
from src.config import GUILD_IDS

class ByTeam(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.split_team = bot.split_team # メインのインスタンスを共有

    @app_commands.command(name="チーム数で分ける")
    @app_commands.guilds(*[discord.Object(id=gid) for gid in GUILD_IDS])
    @app_commands.describe(チーム数 = "任意(default: 2)")

    async def by_team(self, interaction: discord.Interaction, チーム数: int = 2):
        result = self.split_team.split_by_team_count(interaction, チーム数)
        embed = discord.Embed(color=discord.Color.purple())
        embed.add_field(name="チーム数で分ける", value=result, inline=False)
        await interaction.response.send_message(embed=embed)

# Cog 登録
async def setup(bot: commands.Bot):
    await bot.add_cog(ByTeam(bot))