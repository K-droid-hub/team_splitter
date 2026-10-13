import discord, os
from discord.ext import commands
from discord import app_commands

class ResetAll(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.split_team = bot.split_team # メインのインスタンスを共有
    
    guild_ids_raw = os.getenv("GUILD_IDS", "")
    GUILD_IDS = [int(gid.strip()) for gid in guild_ids_raw.split(",") if gid]

    @app_commands.command(name="リセット")
    @app_commands.guilds(*[discord.Object(id=guild_id) for guild_id in GUILD_IDS])

    async def reset_command(self, interaction: discord.Interaction):
        # 実行したサーバーの登録だけ解除する(他サーバーへ波及させない)
        self.split_team.reset_all(interaction)
        embed = discord.Embed(color=discord.Color.black())
        embed.add_field(name="リセット", value="ALL RESET DONE", inline=False)
        await interaction.response.send_message(embed=embed)

# Cog 登録
async def setup(bot: commands.Bot):
    await bot.add_cog(ResetAll(bot))