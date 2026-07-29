import discord, os
from discord import app_commands
from discord.ext import commands

class ByMember(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.split_team = bot.split_team # メインのインスタンスを共有

    guild_ids_raw = os.getenv("GUILD_IDS", "")
    GUILD_IDS = [int(gid.strip()) for gid in guild_ids_raw.split(",") if gid]

    @app_commands.command(name="1チームあたりの人数で分ける")
    @app_commands.guilds(*[discord.Object(id=guild_id) for guild_id in GUILD_IDS])

    @app_commands.describe(人数 = "任意(default: 5)")

    async def by_member(self, interaction: discord.Interaction, 人数:int = 5):
        result = self.split_team.split(interaction, teamCount=0, teamSize=人数)
        embed = discord.Embed(color=discord.Color.purple())
        embed.add_field(name="1チームあたりの人数で分ける", value=result, inline=False)
        await interaction.response.send_message(embed=embed)

# Cog 登録
async def setup(bot: commands.Bot):
    await bot.add_cog(ByMember(bot))