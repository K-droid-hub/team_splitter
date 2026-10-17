import discord
from discord.ext import commands

class ResetAll(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.split_team = bot.split_team # メインのインスタンスを共有

    # 裏コマンド。スラッシュコマンドの一覧には出さない
    @commands.command(name="reset", hidden=True)
    @commands.guild_only()
    async def reset_command(self, ctx):
        # 実行したサーバーの登録だけ解除する(他サーバーへ波及させない)
        self.split_team.reset_all(ctx)
        embed = discord.Embed(color=discord.Color.darker_grey())
        embed.add_field(name="リセット", value="ALL RESET DONE", inline=False)
        await ctx.send(embed=embed, silent=True)

# Cog 登録
async def setup(bot: commands.Bot):
    await bot.add_cog(ResetAll(bot))
