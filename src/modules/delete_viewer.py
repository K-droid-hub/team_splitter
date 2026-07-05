import discord
from discord import app_commands
from discord.ext import commands
from src.config import GUILD_IDS

class DeleteViewer(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.split_team = bot.split_team # メインのインスタンスを共有

    @app_commands.command(name="観戦者解除")
    @app_commands.guilds(*[discord.Object(id=gid) for gid in GUILD_IDS])
    @app_commands.describe(名前="名前")

    async def delete_viewer(self, interaction: discord.Interaction, 名前: str):
        results = self.split_team.delete_viewer(名前)
        embed = discord.Embed(color=discord.Color.blue())
        embed.add_field(name="観戦者解除", value=results, inline=False)
        await interaction.response.send_message(embed=embed)

# Cog 登録
async def setup(bot: commands.Bot):
    await bot.add_cog(DeleteViewer(bot))