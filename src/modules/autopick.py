import discord
from discord import app_commands
from discord.ext import commands
from src.config import GUILD_IDS
from src.modules.drive_ohanap.team_builder import suggest_team
from src.modules.drive_ohanap.game_history import add_game, record_result
from src.modules.drive_ohanap.weight_optimizer import optimize_weights, get_stats, MIN_GAMES


class Autopick(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pending: dict[int, str] = {}  # guild_id → game_id

    @app_commands.command(name="オートピック", description="相手チームを入力して最適な編成を提案します")
    @app_commands.guilds(*[discord.Object(id=gid) for gid in GUILD_IDS])
    @app_commands.describe(相手チーム="相手の5体をコンマ区切りで（例: リザードン,ゲンガー,カメックス,ピカチュウ,ルカリオ）")
    async def autopick(self, interaction: discord.Interaction, 相手チーム: str):
        opponent_team = [n.strip() for n in 相手チーム.split(",") if n.strip()]

        try:
            team, feature_scores, weights_used = suggest_team(opponent_team)
        except Exception as e:
            await interaction.response.send_message(f"エラーが発生しました: {e}", ephemeral=True)
            return

        game_id = add_game(
            guild_id=interaction.guild_id,
            opponent_team=opponent_team,
            suggested_team=team,
            feature_scores=feature_scores,
            weights_used=weights_used,
        )
        self.pending[interaction.guild_id] = game_id

        stats = get_stats()
        embed = discord.Embed(title="オートピック提案", color=discord.Color.blue())
        embed.add_field(name="相手チーム", value="\n".join(opponent_team) or "なし", inline=True)
        embed.add_field(name="提案チーム", value="\n".join(team), inline=True)
        embed.add_field(
            name="カバレッジ",
            value=(
                f"ロール: {int(feature_scores['role'])}種\n"
                f"戦術タグ: {int(feature_scores['tactic'])}種\n"
                f"PS分布: {int(feature_scores['powerspike'])}/3区分"
            ),
            inline=False,
        )
        embed.set_footer(text=f"ID: {game_id} | 累計: {stats['total']}試合 | 勝率: {stats['win_rate']:.0%}")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="結果入力", description="直前のオートピックの試合結果を記録します")
    @app_commands.guilds(*[discord.Object(id=gid) for gid in GUILD_IDS])
    @app_commands.describe(結果="勝ち または 負け")
    @app_commands.choices(結果=[
        app_commands.Choice(name="勝ち", value="win"),
        app_commands.Choice(name="負け", value="loss"),
    ])
    async def record(self, interaction: discord.Interaction, 結果: app_commands.Choice[str]):
        game_id = self.pending.get(interaction.guild_id)
        if not game_id:
            await interaction.response.send_message(
                "記録する試合がありません。先に `/オートピック` を実行してください。",
                ephemeral=True,
            )
            return

        record_result(game_id, 結果.value)
        del self.pending[interaction.guild_id]

        stats = get_stats()
        msg = f"累計: {stats['total']}試合（{stats['wins']}勝 {stats['losses']}敗） | 勝率: {stats['win_rate']:.0%}"

        # MIN_GAMES に達して以降、5試合ごとに自動で重みを最適化
        if stats["total"] >= MIN_GAMES and stats["total"] % 5 == 0:
            new_weights = optimize_weights()
            if new_weights:
                msg += (
                    f"\n\n**重みを自動調整しました**\n"
                    f"ロール: {new_weights['role_weight']:.2f} / "
                    f"戦術: {new_weights['tactic_weight']:.2f} / "
                    f"PS: {new_weights['powerspike_weight']:.2f}"
                )

        embed = discord.Embed(
            title=f"結果記録: {結果.name}",
            description=msg,
            color=discord.Color.green() if 結果.value == "win" else discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Autopick(bot))
