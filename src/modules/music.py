import os

from discord.ext import commands

from src import music_source
from src.music_manager import MAX_QUEUE_SIZE
from src.voice_pool import VoiceSlotUnavailable
from src.voice_session import USE_MUSIC

# !queue で一度に並べる最大件数。長すぎるとメッセージ上限に当たる
QUEUE_PREVIEW_LIMIT = 10


# 許可ユーザーは .env で与える。カンマ区切りの数値以外は無視してログに残す
def load_allowed_user_ids(raw=None):
    raw = os.getenv("MUSIC_ALLOWED_USER_IDS", "") if raw is None else raw
    user_ids = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            user_ids.add(int(part))
        except ValueError:
            print(f"⚠️ MUSIC_ALLOWED_USER_IDS に数値でない値があります: {part}")
    return user_ids


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.music_manager = bot.music_manager
        self.allowed_user_ids = load_allowed_user_ids()
        # 未設定を「全員許可」にすると事故るので、誰も使えない側に倒す
        if not self.allowed_user_ids:
            print("⚠️ MUSIC_ALLOWED_USER_IDS が未設定のため !play は誰も使えません")

    # 隠しコマンドなので、許可外の相手には存在を悟られないよう黙って無視する
    async def cog_check(self, ctx):
        return ctx.author.id in self.allowed_user_ids

    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            print(f"⚠️ 未許可ユーザーの !{ctx.command} を無視しました: {ctx.author.id}")
            return
        print(f"❌ !{ctx.command} でエラー: {error}")

    @commands.command(name="play", hidden=True)
    @commands.guild_only()
    async def play(self, ctx, *, query: str = None):
        if query is None:
            await ctx.send("`!play <URL または検索語>` の形で指定してください。")
            return
        if ctx.author.voice is None:
            await ctx.send("ボイスチャンネルに入ってから実行してください。")
            return
        voice_channel = ctx.author.voice.channel
        # yt-dlp の解決は数秒かかるので、入力中表示で待たせていることを示す
        async with ctx.typing():
            try:
                tracks = await music_source.resolve_query(
                    query, ctx.author.display_name
                )
            except Exception as e:
                print(f"❌ 音源の解決に失敗: {e}")
                await ctx.send("音源を取得できませんでした。URL を確認してください。")
                return
        if not tracks:
            await ctx.send("再生できるものが見つかりませんでした。")
            return
        # そのチャンネルで既に流していれば積むだけ。接続の割り当ては初回だけ行う
        state = self.music_manager.state_for_voice(voice_channel.id)
        if state is None:
            try:
                session = await self.bot.voice_pool.acquire(voice_channel, USE_MUSIC)
            except VoiceSlotUnavailable as e:
                await ctx.send(str(e))
                return
            except Exception as e:
                print(f"❌ VC 接続に失敗: {e}")
                await ctx.send("ボイスチャンネルへの接続に失敗しました。")
                return
            # 接続を待つ間に、同じチャンネルで別の !play が先に始めていればそちらへ積む
            state = self.music_manager.state_for_session(session)
            if state is None:
                state = self.music_manager.start(session, ctx.channel)
        added = self.music_manager.enqueue(state, tracks)
        await ctx.send(self._build_added_message(tracks, added))

    def _build_added_message(self, tracks, added):
        if added == 0:
            return f"キューが上限 ({MAX_QUEUE_SIZE} 件) に達しているため追加できませんでした。"
        if added == 1:
            message = f"キューに追加しました: {tracks[0].title}"
        else:
            message = f"キューに {added} 件追加しました。"
        # 上限で切り捨てた分は黙らずに伝える
        if added < len(tracks):
            message += f"\n(上限のため {len(tracks) - added} 件は追加できませんでした)"
        return message

    # 操作対象の再生を決める。実行者のいるチャンネルを優先し、
    # サーバーで 1 か所しか流していなければそこを対象にする
    def _target_state(self, ctx):
        voice = ctx.author.voice
        if voice is not None:
            state = self.music_manager.state_for_voice(voice.channel.id)
            if state is not None:
                return state, None
        states = self.music_manager.states_in_guild(ctx.guild.id)
        if not states:
            return None, "再生していません。"
        if len(states) > 1:
            return None, "操作したい再生中のボイスチャンネルに入ってから実行してください。"
        return states[0], None

    @commands.command(name="skip", hidden=True)
    @commands.guild_only()
    async def skip(self, ctx):
        state, error = self._target_state(ctx)
        if state is None:
            await ctx.send(error)
            return
        track = state.current
        if not self.music_manager.skip(state):
            await ctx.send("再生中の曲はありません。")
            return
        await ctx.send(f"⏭️ スキップしました: {track.title}")

    @commands.command(name="stop", hidden=True)
    @commands.guild_only()
    async def stop(self, ctx):
        state, error = self._target_state(ctx)
        if state is None:
            await ctx.send(error)
            return
        await self.music_manager.stop(state)
        await ctx.send("⏹️ 音楽を終了しました。")

    @commands.command(name="queue", hidden=True)
    @commands.guild_only()
    async def queue(self, ctx):
        state, error = self._target_state(ctx)
        if state is None:
            await ctx.send(error)
            return
        await ctx.send(self._build_queue_message(state))

    def _build_queue_message(self, state):
        lines = []
        if state.current is not None:
            lines.append(f"▶️ 再生中: {state.current.title}")
        tracks = list(state.queue)
        for index, track in enumerate(tracks[:QUEUE_PREVIEW_LIMIT], start=1):
            length = music_source.format_duration(track.duration)
            requester = f" / {track.requested_by}" if track.requested_by else ""
            lines.append(f"{index}. {track.title} [{length}]{requester}")
        remaining = len(tracks) - QUEUE_PREVIEW_LIMIT
        if remaining > 0:
            lines.append(f"...他 {remaining} 件")
        if not lines:
            lines.append("キューは空です。")
        return "\n".join(lines)

    # 誰もいないチャンネルへ流し続けないよう、無人になったら音楽をやめて枠を返す。
    # 読み上げ側(src/modules/tts.py)と同じ考え方
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        # 再生中のチャンネルから離れた変化だけを見る。ミュート切り替えは対象外
        if before.channel is None:
            return
        if after.channel is not None and after.channel.id == before.channel.id:
            return
        state = self.music_manager.state_for_voice(before.channel.id)
        if state is None:
            return
        # 接続しているのが 2 体目の bot でも、メンバーはメイン bot 側から数えられる
        channel = self.bot.get_channel(before.channel.id)
        if channel is None or any(not m.bot for m in channel.members):
            return
        await self.music_manager.stop(state)


# Cog 登録
async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
