import asyncio, discord, os, ssl, certifi
from dotenv import load_dotenv
from discord.ext import commands
from src.grouping import SplitTeam # コンテナ化すると src. を指定する必要あり。vscode 直実行では不要
from src.music_manager import MusicManager
from src.panel_store import PanelStore
from src.tts_manager import TtsManager
from src.voice_pool import VoicePool

ssl_context = ssl.create_default_context(cafile=certifi.where())
ssl._create_default_https_context = lambda: ssl_context


def load_guild_ids():
    guild_ids_raw = os.getenv("GUILD_IDS", "")
    return [int(gid.strip()) for gid in guild_ids_raw.split(",") if gid]


# 声を貸すだけの 2 体目の bot。コマンドも発言の読み取りも行わず、
# メイン bot が受けた読み上げ・音楽をミキサー経由で流すのに接続だけを使う。
# 別アカウントなので、同じサーバーでもう 1 つのボイスチャンネルに入れる
def build_player_client(guild_ids):
    intents = discord.Intents.none()
    intents.guilds = True # VC とチャンネルのキャッシュに必要
    intents.voice_states = True # 接続状態の追跡に必要
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"PLAYER READY: {client.user}")
        # 意図的に 1 体だけ招待するサーバーもあるので警告にはしない。
        # 招待漏れに気づけるよう、どこが 1 チャンネル運用になるかは毎回見せる
        missing = [gid for gid in guild_ids if client.get_guild(gid) is None]
        if missing:
            print(f"ℹ️ 2 体目の bot がいないサーバー(1 チャンネルで動作): {missing}")

    return client


class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True # メッセージ内容取得
        intents.voice_states = True # VC 内のメンバー取得
        intents.members = True # サーバーメンバー取得
        super().__init__(command_prefix="!", intents=intents)
        self.split_team = SplitTeam()
        self.tts_manager = TtsManager()
        self.music_manager = MusicManager()
        # 設置済みパネルの位置。再起動後も古いパネルを消せるよう永続化する
        self.panel_store = PanelStore()
        # 2 体目のトークンが無いときは単独で動き、どのサーバーも 1 チャンネルまでになる。
        # 読み上げと音楽はミキサーで重ねるので、1 体でも同じチャンネルなら両方使える
        self.player_token = os.getenv("PLAYER_BOT_TOKEN")
        self.player_client = (
            build_player_client(load_guild_ids()) if self.player_token else None
        )
        # 並び順が優先順位。メイン bot から使い、2 体目は 2 つ目のチャンネルでだけ使う
        self.voice_pool = VoicePool([self, self.player_client])

    async def setup_hook(self):
        # モジュールのロード
        modules = [
            'src.modules.by_team',
            'src.modules.by_member',
            'src.modules.by_ramdom',
            'src.modules.add_viewer',
            'src.modules.delete_viewer',
            'src.modules.add_priority',
            'src.modules.delete_priority',
            'src.modules.show_all',
            'src.modules.reset_all',
            'src.modules.tts',
            'src.modules.panel',
            'src.modules.music',
        ]
        for module in modules:
            try:
                await self.load_extension(module)
                print (f"✅ Loaded module: {module}")
            except Exception as e:
                print(f"❌ Failed to load {module}: {e}")

        # Cogのロード完了後に、ギルドごとにsync
        for guild_id in load_guild_ids():
            guild = discord.Object(id=guild_id)
            try:
                synced = await self.tree.sync(guild=guild)
                print(f"SYNC OK ({guild_id}): {len(synced)}")
            except discord.Forbidden:
                print(f"⚠️ SYNC SKIP ({guild_id}): Bot が未参加か applications.commands 未付与")

    async def on_ready(self):
        print(f"READY: {self.user}")
        activity = discord.Game(name="Pokémon UNITE")
        await self.change_presence(status=discord.Status.online, activity=activity)
        print(f"PID: {os.getpid()}")
        print('------------')

# TtsManager 生成時に .env の VOICEVOX 設定を読めるよう、bot 生成前にロードする
load_dotenv()
bot = MyBot()
@bot.command(name="hello") # 運営用 !hello コマンド
async def show_command(ctx: commands.Context):
    await ctx.send("HELLO")

# 起動
TOKEN = os.getenv('DISCORD_TOKEN')


# メイン bot と再生用 bot を 1 プロセスで並行して動かす。コンテナを増やさずに
# 2 アカウント分のボイス接続を確保するため
async def main():
    if bot.player_client is None:
        print("ℹ️ PLAYER_BOT_TOKEN が未設定のため、どのサーバーも 1 チャンネルで動作します")
        await bot.start(TOKEN)
        return
    try:
        await asyncio.gather(
            bot.start(TOKEN),
            bot.player_client.start(bot.player_token),
        )
    finally:
        # 片方が落ちてももう片方を閉じ、接続が宙に浮いたままにしない
        if not bot.player_client.is_closed():
            await bot.player_client.close()
        if not bot.is_closed():
            await bot.close()


asyncio.run(main())
