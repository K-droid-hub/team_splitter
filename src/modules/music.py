from discord.ext import commands

from src import music_source
from src.music_manager import MAX_QUEUE_SIZE
from src.voice_pool import VoiceSlotUnavailable
from src.voice_session import USE_MUSIC

# !queue で一度に並べる最大件数。長すぎるとメッセージ上限に当たる
QUEUE_PREVIEW_LIMIT = 10
# !volume で指定する目盛りの範囲(1〜100)。細かく選べるよう 100 段階にしている
MAX_MUSIC_VOLUME = 100
# 目盛り 100 が当たる実際の音量(%)。音楽は大きくなりがちなので、読み上げが埋もれないよう抑える(従来の 50% の 3/4)
MUSIC_VOLUME_CEILING = 37.5


# 目盛り → 実際の音量(%)
def scale_to_percent(scale):
    return scale * MUSIC_VOLUME_CEILING / MAX_MUSIC_VOLUME


# 実際の音量(%) → 目盛り。表示用なので四捨五入する
def percent_to_scale(percent):
    return round(percent * MAX_MUSIC_VOLUME / MUSIC_VOLUME_CEILING)


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.music_manager = bot.music_manager

    @commands.command(name="play", hidden=True)
    @commands.guild_only()
    async def play(self, ctx, *, query: str = None):
        if query is None:
            await ctx.send("`!play <URL または検索語>` の形で指定してください。", silent=True)
            return
        if ctx.author.voice is None:
            await ctx.send("ボイスチャンネルに入ってから実行してください。", silent=True)
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
                await ctx.send("音源を取得できませんでした。URL を確認してください。", silent=True)
                return
        if not tracks:
            await ctx.send("再生できるものが見つかりませんでした。", silent=True)
            return
        # そのチャンネルで既に流していれば積むだけ。接続の割り当ては初回だけ行う
        state = self.music_manager.state_for_voice(voice_channel.id)
        if state is None:
            try:
                session = await self.bot.voice_pool.acquire(voice_channel, USE_MUSIC)
            except VoiceSlotUnavailable as e:
                await ctx.send(str(e), silent=True)
                return
            except Exception as e:
                print(f"❌ VC 接続に失敗: {e}")
                await ctx.send("ボイスチャンネルへの接続に失敗しました。", silent=True)
                return
            # 接続を待つ間に、同じチャンネルで別の !play が先に始めていればそちらへ積む
            state = self.music_manager.state_for_session(session)
            if state is None:
                state = self.music_manager.start(session, ctx.channel)
        added = self.music_manager.enqueue(state, tracks)
        await ctx.send(self._build_added_message(tracks, added), silent=True)

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

    # 操作対象の再生は、実行者がいるボイスチャンネルのものに限る。
    # 誰でも操作できる代わりに、VC に入っていない人からの操作は弾く
    def _target_state(self, ctx):
        voice = ctx.author.voice
        if voice is None:
            return None, "ボイスチャンネルに入ってから実行してください。"
        state = self.music_manager.state_for_voice(voice.channel.id)
        if state is None:
            return None, "このボイスチャンネルでは再生していません。"
        return state, None

    @commands.command(name="skip", hidden=True)
    @commands.guild_only()
    async def skip(self, ctx):
        state, error = self._target_state(ctx)
        if state is None:
            await ctx.send(error, silent=True)
            return
        track = state.current
        if not self.music_manager.skip(state):
            await ctx.send("再生中の曲はありません。", silent=True)
            return
        await ctx.send(f"⏭️ スキップしました: {track.title}", silent=True)

    @commands.command(name="stop", hidden=True)
    @commands.guild_only()
    async def stop(self, ctx):
        state, error = self._target_state(ctx)
        if state is None:
            await ctx.send(error, silent=True)
            return
        await self.music_manager.stop(state)
        await ctx.send("⏹️ 音楽を終了しました。", silent=True)

    # 音量は 1〜MAX_MUSIC_VOLUME の目盛りで指定する。省略すると今の目盛りを返す
    @commands.command(name="volume", hidden=True)
    @commands.guild_only()
    async def volume(self, ctx, value: str = None):
        state, error = self._target_state(ctx)
        if state is None:
            await ctx.send(error, silent=True)
            return
        if value is None:
            scale = percent_to_scale(state.session.music_volume())
            await ctx.send(f"🔈 現在の音量: {scale} / {MAX_MUSIC_VOLUME}", silent=True)
            return
        try:
            percent = int(value)
        except ValueError:
            percent = None
        if percent is None or not 1 <= percent <= MAX_MUSIC_VOLUME:
            await ctx.send(f"`!volume <1〜{MAX_MUSIC_VOLUME}>` の形で指定してください。", silent=True)
            return
        state.session.set_music_volume(scale_to_percent(percent))
        await ctx.send(f"🔊 音楽の音量を {percent} / {MAX_MUSIC_VOLUME} にしました。", silent=True)

    # 今の曲を次にもう一度流す。キューの先頭へ積むだけなので、順番待ちの曲より先に鳴る
    @commands.command(name="repeat", hidden=True)
    @commands.guild_only()
    async def repeat(self, ctx):
        state, error = self._target_state(ctx)
        if state is None:
            await ctx.send(error, silent=True)
            return
        if state.current is None:
            await ctx.send("再生中の曲はありません。", silent=True)
            return
        if not self.music_manager.repeat_next(state):
            await ctx.send(f"キューが上限 ({MAX_QUEUE_SIZE} 件) に達しているため追加できませんでした。", silent=True)
            return
        await ctx.send(f"🔁 次にもう一度再生します: {state.current.title}", silent=True)

    # 今の曲をループする。もう一度押すか、!skip・!stop で解除される
    @commands.command(name="loop", hidden=True)
    @commands.guild_only()
    async def loop(self, ctx):
        state, error = self._target_state(ctx)
        if state is None:
            await ctx.send(error, silent=True)
            return
        if state.current is None:
            await ctx.send("再生中の曲はありません。", silent=True)
            return
        state.loop = not state.loop
        if state.loop:
            await ctx.send(f"🔁 ループを開始しました: {state.current.title}", silent=True)
        else:
            await ctx.send("ループを解除しました。", silent=True)

    @commands.command(name="queue", hidden=True)
    @commands.guild_only()
    async def queue(self, ctx):
        state, error = self._target_state(ctx)
        if state is None:
            await ctx.send(error, silent=True)
            return
        await ctx.send(self._build_queue_message(state), silent=True)

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
