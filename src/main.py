import discord, os, ssl, certifi
from dotenv import load_dotenv
from discord.ext import commands
from src.grouping import SplitTeam # コンテナ化すると src. を指定する必要あり。vscode 直実行では不要
from src.tts_manager import TtsManager

ssl_context = ssl.create_default_context(cafile=certifi.where())
ssl._create_default_https_context = lambda: ssl_context

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True # メッセージ内容取得
        intents.voice_states = True # VC 内のメンバー取得
        intents.members = True # サーバーメンバー取得
        super().__init__(command_prefix="!", intents=intents)
        self.split_team = SplitTeam()
        self.tts_manager = TtsManager()

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
        ]
        for module in modules:
            try:
                await self.load_extension(module)
                print (f"✅ Loaded module: {module}")
            except Exception as e:
                print(f"❌ Failed to load {module}: {e}")

        # Cogのロード完了後に、ギルドごとにsync
        guild_ids_raw = os.getenv("GUILD_IDS", "")
        GUILD_IDS = [int(gid.strip()) for gid in guild_ids_raw.split(",") if gid]
        for guild_id in GUILD_IDS:
            guild = discord.Object(id=guild_id)
            synced = await self.tree.sync(guild=guild)
            print(f"SYNC OK ({guild_id}): {len(synced)}")

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

@bot.command(name="bye") # 運営用 !bye コマンド
async def show_command(ctx: commands.Context):
    await ctx.send("SHUTTING DOWN...")
    await bot.close()

# 起動
TOKEN = os.getenv('DISCORD_TOKEN')
bot.run(TOKEN)