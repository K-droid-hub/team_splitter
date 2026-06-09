import discord
from discord import app_commands
from discord.ext import commands
from src.config import GUILD_IDS

class Autopick(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.split_team = bot.split_team # メインのインスタンスを共有

    @app_commands.command(name="オートピック")
    @app_commands.guilds(*[discord.Object(id=gid) for gid in GUILD_IDS])

    async def autopick(self, interaction: discord.Interaction, 相手チーム: str):
        results = self.split_team.autopick(相手チーム.split(','))
        embed = discord.Embed(color=discord.Color.blue())
        embed.add_field(name="オートピック", value=results, inline=False)
        await interaction.response.send_message(embed=embed)

# Cog 登録
async def setup(bot: commands.Bot):
    await bot.add_cog(Autopick(bot))