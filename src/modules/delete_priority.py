import discord
from discord import app_commands
from discord.ext import commands
from src.config import GUILD_IDS

class DeletePriority(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.split_team = bot.split_team # メインのインスタンスを共有

    @app_commands.command(name="優先者解除")
    @app_commands.guilds(*[discord.Object(id=gid) for gid in GUILD_IDS])

    async def delete_priority(self, interaction: discord.Interaction, 名前: str):
        results = self.split_team.delete_special(名前, isViewer=False, isPriority=True)
        embed = discord.Embed(color=discord.Color.blue())
        embed.add_field(name="優先から削除", value=results, inline=False)
        await interaction.response.send_message(embed=embed)

# Cog 登録
async def setup(bot: commands.Bot):
    await bot.add_cog(DeletePriority(bot))