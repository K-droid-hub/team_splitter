import discord, os
from discord.ext import commands
from discord import app_commands

from src.grouping import format_member_list  # 意気込み付きの一覧整形を共有

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
        members = self.split_team.channel_members
        # 各状態のリストを取得(意気込みがある人は名前の下に斜体で添える)
        rockedViewers = format_member_list([m for m in members if m.viewer and m.rockedFlag])
        viewers = format_member_list([m for m in members if m.viewer and not m.rockedFlag])
        rockedPriorities = format_member_list([m for m in members if m.priority and m.rockedFlag])
        priorities = format_member_list([m for m in members if m.priority and not m.rockedFlag])
        channel_members = format_member_list([m for m in members if not m.viewer and not m.priority])
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