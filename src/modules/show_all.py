import discord, os
from discord.ext import commands
from discord import app_commands

class ShowAll(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.split_team = bot.split_team # メインのインスタンスを共有

    guild_ids_raw = os.getenv("GUILD_IDS", "")
    GUILD_IDS = [int(gid.strip()) for gid in guild_ids_raw.split(",") if gid]
    
    @app_commands.command(name="一覧表示")
    @app_commands.guilds(*[discord.Object(id=guild_id) for guild_id in GUILD_IDS])

    async def show_command(self, interaction: discord.Interaction):
        self.split_team.set_member(interaction)
        # 各状態のリストを取得
        rockedViewers = ", ".join([member.name for member in self.split_team.channel_members if member.viewer and member.rockedFlag]) if self.split_team.channel_members else "なし"
        viewers = ", ".join([member.name for member in self.split_team.channel_members if member.viewer and not member.rockedFlag]) if self.split_team.channel_members else "なし"
        rockedPriorities = ", ".join([member.name for member in self.split_team.channel_members if member.priority and member.rockedFlag]) if self.split_team.channel_members else "なし"
        priorities = ", ".join([member.name for member in self.split_team.channel_members if member.priority and not member.rockedFlag]) if self.split_team.channel_members else "なし"
        channel_members = ", ".join([member.name for member in self.split_team.channel_members if not member.viewer and not member.priority]) if self.split_team.channel_members else "なし"
        embed = discord.Embed(title="一覧表示", color=discord.Color.red())
        embed.add_field(name="チャンネルメンバー", value=channel_members, inline=False)
        embed.add_field(name="固定観戦者", value=rockedViewers, inline=False)
        embed.add_field(name="次だけ観戦者", value=viewers, inline=False)
        embed.add_field(name="固定優先者", value=rockedPriorities, inline=False)
        embed.add_field(name="次だけ優先者", value=priorities, inline=False)
        await interaction.response.send_message(embed=embed)

# Cog 登録
async def setup(bot: commands.Bot):
    await bot.add_cog(ShowAll(bot))