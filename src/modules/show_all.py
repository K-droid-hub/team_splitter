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
        # VC チェックを省くと、前回別の VC で作ったリストが公開チャンネルに出てしまう
        if not self.split_team.set_member(interaction):
            await interaction.response.send_message(self.split_team.vc_err, ephemeral=True)
            return
        members = self.split_team.channel_members
        # 各状態のリストを取得(意気込みがある人は名前の下に斜体で添える)
        sections = [
            ("チャンネルメンバー", [m for m in members if not m.viewer and not m.priority]),
            ("固定観戦者", [m for m in members if m.viewer and m.rockedFlag]),
            ("次だけ観戦者", [m for m in members if m.viewer and not m.rockedFlag]),
            ("固定優先者", [m for m in members if m.priority and m.rockedFlag]),
            ("次だけ優先者", [m for m in members if m.priority and not m.rockedFlag]),
        ]
        embed = discord.Embed(title="一覧表示", color=discord.Color.blue())
        # 「なし」が並ぶと読みにくいので、該当者がいるセクションだけ出す
        for name, sectionMembers in sections:
            if sectionMembers:
                embed.add_field(
                    name=name, value=format_member_list(sectionMembers), inline=False
                )
        if not embed.fields:
            embed.description = "表示できるメンバーがいません。"
        await interaction.response.send_message(embed=embed)

# Cog 登録
async def setup(bot: commands.Bot):
    await bot.add_cog(ShowAll(bot))