import discord
from discord import app_commands
from discord.ext import commands
from src.config import GUILD_IDS

class AddViewer(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.split_team = bot.split_team # メインのインスタンスを共有

    @app_commands.command(name="観戦者登録")
    @app_commands.guilds(*[discord.Object(id=gid) for gid in GUILD_IDS])
    @app_commands.describe(ロック = "する or しない")

    async def add_viewer(self, interaction: discord.Interaction, 名前: str, ロック: str = "しない"):
        isRocked = ロック == "する"
        results = self.split_team.add_special(interaction, addName=名前, isViewer=True, isPriority=False, isRocked=isRocked)
        embed = discord.Embed(color=discord.Color.blue())
        embed.add_field(name="観戦者登録", value=results, inline=False)
        await interaction.response.send_message(embed=embed)

# Cog 登録
async def setup(bot: commands.Bot):
    await bot.add_cog(AddViewer(bot))