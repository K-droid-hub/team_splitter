import discord
from discord.ext import commands
from discord import app_commands
from src.config import GUILD_IDS

class ResetAll(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.split_team = bot.split_team # メインのインスタンスを共有

    @app_commands.command(name="リセット")
    @app_commands.guilds(*[discord.Object(id=gid) for gid in GUILD_IDS])

    async def reset_command(self, interaction: discord.Interaction):
        self.split_team.reset_all()
        embed = discord.Embed(color=discord.Color.black())
        embed.add_field(name="リセット", value="ALL RESET DONE", inline=False)
        await interaction.response.send_message(embed=embed)

# Cog 登録
async def setup(bot: commands.Bot):
    await bot.add_cog(ResetAll(bot))