import discord
from discord import app_commands
from discord.ext import commands
from src.config import GUILD_IDS

class AddPriority(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.split_team = bot.split_team # メインのインスタンスを共有

    # メンバーを優先者リストに追加
    @app_commands.command(name="優先者登録")
    @app_commands.guilds(*[discord.Object(id=gid) for gid in GUILD_IDS])
    @app_commands.describe(ロック = "する or しない")

    async def priority_member(self, interaction: discord.Interaction, 名前: str, ロック: str = "しない"):
        isRocked = ロック == "する"
        results = self.split_team.add_priority(interaction, 名前, lock=isRocked)
        embed = discord.Embed(color=discord.Color.blue())
        embed.add_field(name="優先者登録", value=results, inline=False)
        await interaction.response.send_message(embed=embed)

# Cog 登録
async def setup(bot: commands.Bot):
    await bot.add_cog(AddPriority(bot))