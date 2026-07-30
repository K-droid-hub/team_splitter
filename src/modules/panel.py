import discord, os
from discord import app_commands
from discord.ext import commands

from src.grouping import format_member_list  # 一覧表示の意気込み整形を共有
from src.modules.tts import SPEAKER_CHOICES  # 読み上げ声設定の話者一覧を共有
from src.pokemons import get_pokemons, pick_pokemons, to_japanese_name

MAX_NUMBER_BUTTONS = 5  # 数値ボタンの最大表示数
MAX_PICK_COUNT = 5  # ランダムピックで一度に選べる最大数(1 チーム分)
MEMBER_BUTTON_LIMIT_SINGLE = 20  # 登録系(単一選択 + 操作ボタン)でボタン表示する上限
MEMBER_BUTTON_LIMIT_MULTI = 15  # 抽選(数値行 + 複数選択 + 実行)でボタン表示する上限
MEMBER_BUTTON_LIMIT_DELETE = 20  # 解除系(複数選択 + 実行)でボタン表示する上限


def build_number_values(mode, member_size):
    # チーム数は 2 以上、人数は 1 以上。VC 人数を上限に、ボタンは最大 5 個まで
    start = 2 if mode == "team" else 1
    return list(range(start, member_size + 1))[:MAX_NUMBER_BUTTONS]


def build_pick_counts():
    # ランダムピックの抽選数候補。VC 人数に依存しない固定値
    return list(range(1, MAX_PICK_COUNT + 1))


def make_split_embed(title, result):
    embed = discord.Embed(color=discord.Color.purple())
    embed.add_field(name=title, value=result, inline=False)
    return embed


def make_special_embed(title, result):
    embed = discord.Embed(color=discord.Color.blue())
    embed.add_field(name=title, value=result, inline=False)
    return embed


def make_panel_embed():
    embed = discord.Embed(
        title="チーム分けパネル",
        description="下のボタンから操作できます。ボイスチャンネルに入ってから押してください。",
        color=discord.Color.green(),
    )
    return embed


def make_manual_embed():
    line = "━" * 15
    embed = discord.Embed(
        title="📖 チーム分け Bot 説明書",
        description=f"{line}\nボタン操作とコマンドの一覧\n{line}",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="チーム分け",
        value=(
            "**チーム数で分ける**: *指定したチーム数でメンバーを均等に分けます*\n"
            "**人数で分ける**: *1 チームあたりの人数を指定して分けます*\n"
            "**人数分抽選**: *指定した人数をランダムに抽選します(優先者を指定可)*\n"
            "**ランダムピック**: *参戦ポケモンから指定数をランダムに選びます*"
        ),
        inline=False,
    )
    embed.add_field(
        name="メンバー設定",
        value=(
            "**観戦者登録**: *選んだメンバーを観戦者(チーム分け対象外)にします*\n"
            "**優先者登録**: *選んだメンバーを優先者にします*\n"
            "**観戦者解除 / 優先者解除**: *登録を解除します*\n"
            "**一覧表示**: *現在の登録状況を表示します*"
        ),
        inline=False,
    )
    embed.add_field(
        name="読み上げ(TTS)",
        value=(
            "**読み上げ声設定**: *あなたの読み上げ音声を選びます*\n"
            "**読み上げ開始 / 終了**: *パネルのボタンで読み上げを切り替えます*\n"
            "**/読み上げ開始**: *入退室通知ありなど詳細指定で開始します*\n"
            "**/読み上げ終了**: *読み上げを終了します*"
        ),
        inline=False,
    )
    embed.add_field(
        name="コマンド",
        value=(
            "**/パネル**: *操作パネルを設置します*\n"
            "**/リセット**: *観戦者・優先者の登録を全て解除します*"
        ),
        inline=False,
    )
    return embed


# ---- 数値選択(チーム数 / 人数): 押下即実行 ----
class NumberButton(discord.ui.Button):
    def __init__(self, value, row):
        super().__init__(label=str(value), style=discord.ButtonStyle.primary, row=row)
        self.value = value

    async def callback(self, interaction):
        view = self.view
        await view.cog.run_split(interaction, view.mode, self.value)


class NumberSelectView(discord.ui.View):
    def __init__(self, cog, mode, values):
        super().__init__(timeout=120)
        self.cog = cog
        self.mode = mode  # "team" or "size"
        for value in values:
            self.add_item(NumberButton(value, row=0))


# ---- ランダムピック: 抽選数を押下即実行 ----
class PickCountButton(discord.ui.Button):
    def __init__(self, value, row):
        super().__init__(label=str(value), style=discord.ButtonStyle.primary, row=row)
        self.value = value

    async def callback(self, interaction):
        await self.view.cog.run_random_pick(interaction, self.value)


class RandomPickView(discord.ui.View):
    def __init__(self, cog, counts):
        super().__init__(timeout=120)
        self.cog = cog
        for value in counts:
            self.add_item(PickCountButton(value, row=0))


# ---- 人数分抽選: 人数(単一) + 優先者(複数) + 実行 ----
class SizeButton(discord.ui.Button):
    def __init__(self, value, row):
        super().__init__(label=str(value), style=discord.ButtonStyle.secondary, row=row)
        self.value = value

    async def callback(self, interaction):
        view = self.view
        view.selected_size = self.value
        for item in view.children:
            if isinstance(item, SizeButton):
                item.style = (
                    discord.ButtonStyle.success
                    if item.value == self.value
                    else discord.ButtonStyle.secondary
                )
        await interaction.response.edit_message(view=view)


class PriorityToggleButton(discord.ui.Button):
    def __init__(self, name, row):
        super().__init__(label=name, style=discord.ButtonStyle.secondary, row=row)
        self.name = name

    async def callback(self, interaction):
        view = self.view
        if self.name in view.selected_names:
            view.selected_names.discard(self.name)
            self.style = discord.ButtonStyle.secondary
        else:
            view.selected_names.add(self.name)
            self.style = discord.ButtonStyle.success
        await interaction.response.edit_message(view=view)


class PriorityMultiSelect(discord.ui.Select):
    # メンバーが多くボタンで収まらない場合のフォールバック
    def __init__(self, names, row):
        options = [discord.SelectOption(label=name) for name in names[:25]]
        super().__init__(
            placeholder="優先者を選択(任意・複数可)",
            min_values=0,
            max_values=len(options),
            options=options,
            row=row,
        )

    async def callback(self, interaction):
        self.view.selected_names = set(self.values)
        await interaction.response.defer()


class RandomExecuteButton(discord.ui.Button):
    def __init__(self, row):
        super().__init__(label="実行", style=discord.ButtonStyle.primary, row=row)

    async def callback(self, interaction):
        view = self.view
        if view.selected_size is None:
            await interaction.response.send_message("人数を選んでください", ephemeral=True)
            return
        await view.cog.run_random(interaction, view.selected_size, list(view.selected_names))


class RandomView(discord.ui.View):
    def __init__(self, cog, size_values, member_names):
        super().__init__(timeout=120)
        self.cog = cog
        self.selected_size = None
        self.selected_names = set()
        for value in size_values:
            self.add_item(SizeButton(value, row=0))
        if len(member_names) <= MEMBER_BUTTON_LIMIT_MULTI:
            for i, name in enumerate(member_names):
                self.add_item(PriorityToggleButton(name, row=1 + i // 5))
            execute_row = 4
        else:
            self.add_item(PriorityMultiSelect(member_names, row=1))
            execute_row = 2
        self.add_item(RandomExecuteButton(row=execute_row))


# ---- 観戦者 / 優先者 登録: 対象(単一) + ロック + 実行 ----
class RegisterMemberButton(discord.ui.Button):
    def __init__(self, name, row):
        super().__init__(label=name, style=discord.ButtonStyle.secondary, row=row)
        self.name = name

    async def callback(self, interaction):
        view = self.view
        view.selected_name = self.name
        for item in view.children:
            if isinstance(item, RegisterMemberButton):
                item.style = (
                    discord.ButtonStyle.success
                    if item.name == self.name
                    else discord.ButtonStyle.secondary
                )
        await interaction.response.edit_message(view=view)


class RegisterMemberSelect(discord.ui.Select):
    def __init__(self, names, row):
        options = [discord.SelectOption(label=name) for name in names[:25]]
        super().__init__(
            placeholder="対象を選択", min_values=1, max_values=1, options=options, row=row
        )

    async def callback(self, interaction):
        self.view.selected_name = self.values[0]
        await interaction.response.defer()


class LockToggleButton(discord.ui.Button):
    def __init__(self, row):
        super().__init__(label="ロック: しない", style=discord.ButtonStyle.secondary, row=row)

    async def callback(self, interaction):
        view = self.view
        view.lock = not view.lock
        self.label = "ロック: する" if view.lock else "ロック: しない"
        self.style = discord.ButtonStyle.success if view.lock else discord.ButtonStyle.secondary
        await interaction.response.edit_message(view=view)


class RegisterExecuteButton(discord.ui.Button):
    def __init__(self, row):
        super().__init__(label="実行", style=discord.ButtonStyle.primary, row=row)

    async def callback(self, interaction):
        view = self.view
        if view.selected_name is None:
            await interaction.response.send_message("対象を選んでください", ephemeral=True)
            return
        await view.cog.run_register(interaction, view.is_viewer, view.selected_name, view.lock)


class RegisterView(discord.ui.View):
    def __init__(self, cog, is_viewer, member_names):
        super().__init__(timeout=120)
        self.cog = cog
        self.is_viewer = is_viewer
        self.selected_name = None
        self.lock = False
        if len(member_names) <= MEMBER_BUTTON_LIMIT_SINGLE:
            for i, name in enumerate(member_names):
                self.add_item(RegisterMemberButton(name, row=i // 5))
        else:
            self.add_item(RegisterMemberSelect(member_names, row=0))
        self.add_item(LockToggleButton(row=4))
        self.add_item(RegisterExecuteButton(row=4))


# ---- 観戦者 / 優先者 解除: 対象(複数) + 実行 ----
class DeleteToggleButton(discord.ui.Button):
    def __init__(self, name, row):
        super().__init__(label=name, style=discord.ButtonStyle.secondary, row=row)
        self.name = name

    async def callback(self, interaction):
        view = self.view
        if self.name in view.selected_names:
            view.selected_names.discard(self.name)
            self.style = discord.ButtonStyle.secondary
        else:
            view.selected_names.add(self.name)
            self.style = discord.ButtonStyle.success
        await interaction.response.edit_message(view=view)


class DeleteMultiSelect(discord.ui.Select):
    # メンバーが多くボタンで収まらない場合のフォールバック
    def __init__(self, names, row):
        options = [discord.SelectOption(label=name) for name in names[:25]]
        super().__init__(
            placeholder="解除する対象を選択(複数可)",
            min_values=0,
            max_values=len(options),
            options=options,
            row=row,
        )

    async def callback(self, interaction):
        self.view.selected_names = set(self.values)
        await interaction.response.defer()


class DeleteExecuteButton(discord.ui.Button):
    def __init__(self, row):
        super().__init__(label="実行", style=discord.ButtonStyle.primary, row=row)

    async def callback(self, interaction):
        view = self.view
        if not view.selected_names:
            await interaction.response.send_message("対象を選んでください", ephemeral=True)
            return
        await view.cog.run_delete(interaction, view.is_viewer, list(view.selected_names))


class DeleteView(discord.ui.View):
    def __init__(self, cog, is_viewer, member_names):
        super().__init__(timeout=120)
        self.cog = cog
        self.is_viewer = is_viewer
        self.selected_names = set()
        if len(member_names) <= MEMBER_BUTTON_LIMIT_DELETE:
            for i, name in enumerate(member_names):
                self.add_item(DeleteToggleButton(name, row=i // 5))
            execute_row = 4
        else:
            self.add_item(DeleteMultiSelect(member_names, row=0))
            execute_row = 1
        self.add_item(DeleteExecuteButton(row=execute_row))


# ---- 読み上げ声設定: 話者を押下即設定(本人のみ) ----
class VoiceButton(discord.ui.Button):
    def __init__(self, speaker_name, speaker_value):
        super().__init__(label=speaker_name, style=discord.ButtonStyle.primary)
        self.speaker_name = speaker_name
        self.speaker_value = speaker_value

    async def callback(self, interaction):
        await self.view.cog.run_set_voice(interaction, self.speaker_value, self.speaker_name)


class VoiceSelectView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=120)
        self.cog = cog
        for choice in SPEAKER_CHOICES:
            self.add_item(VoiceButton(choice.name, choice.value))


# ---- 意気込み入力(モーダル) ----
class AspirationModal(discord.ui.Modal, title="意気込みを入力"):
    def __init__(self, cog, current=""):
        super().__init__()
        self.cog = cog
        # 既存の意気込みを初期値に入れ、空にして送れば削除できるようにする
        self.aspiration = discord.ui.TextInput(
            label="意気込み",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=100,
            default=current,
            placeholder="例: 今日は絶対に勝つ!",
        )
        self.add_item(self.aspiration)

    async def on_submit(self, interaction):
        await self.cog.run_set_aspiration(interaction, self.aspiration.value)


# ---- 常駐パネル(永続 View) ----
class PanelView(discord.ui.View):
    def __init__(self, cog, is_reading=False):
        super().__init__(timeout=None)
        self.cog = cog
        # 読み上げ状態に応じて 1 つのトグルボタンの見た目を切り替える
        if is_reading:
            self.tts_toggle.label = "読み上げ終了"
            self.tts_toggle.style = discord.ButtonStyle.danger
        else:
            self.tts_toggle.label = "読み上げ開始"
            self.tts_toggle.style = discord.ButtonStyle.success

    @discord.ui.button(label="チーム数で分ける", style=discord.ButtonStyle.primary,
                       custom_id="panel:by_team", row=0)
    async def by_team(self, interaction, button):
        await self.cog.open_number(interaction, "team")

    @discord.ui.button(label="人数で分ける", style=discord.ButtonStyle.primary,
                       custom_id="panel:by_size", row=0)
    async def by_size(self, interaction, button):
        await self.cog.open_number(interaction, "size")

    @discord.ui.button(label="人数分抽選", style=discord.ButtonStyle.primary,
                       custom_id="panel:by_random", row=0)
    async def by_random(self, interaction, button):
        await self.cog.open_random(interaction)

    @discord.ui.button(label="ランダムピック", style=discord.ButtonStyle.primary,
                       custom_id="panel:random_pick", row=0)
    async def random_pick(self, interaction, button):
        await self.cog.open_random_pick(interaction)

    @discord.ui.button(label="観戦者登録", style=discord.ButtonStyle.secondary,
                       custom_id="panel:add_viewer", row=1)
    async def add_viewer(self, interaction, button):
        await self.cog.open_register(interaction, is_viewer=True)

    @discord.ui.button(label="優先者登録", style=discord.ButtonStyle.secondary,
                       custom_id="panel:add_priority", row=1)
    async def add_priority(self, interaction, button):
        await self.cog.open_register(interaction, is_viewer=False)

    @discord.ui.button(label="観戦者解除", style=discord.ButtonStyle.secondary,
                       custom_id="panel:del_viewer", row=2)
    async def del_viewer(self, interaction, button):
        await self.cog.open_delete(interaction, is_viewer=True)

    @discord.ui.button(label="優先者解除", style=discord.ButtonStyle.secondary,
                       custom_id="panel:del_priority", row=2)
    async def del_priority(self, interaction, button):
        await self.cog.open_delete(interaction, is_viewer=False)

    @discord.ui.button(label="一覧表示", style=discord.ButtonStyle.success,
                       custom_id="panel:show_all", row=3)
    async def show_all(self, interaction, button):
        await self.cog.run_show_all(interaction)

    @discord.ui.button(label="意気込み設定", style=discord.ButtonStyle.secondary,
                       custom_id="panel:aspiration", row=3)
    async def aspiration(self, interaction, button):
        await self.cog.open_aspiration(interaction)

    @discord.ui.button(label="読み上げ声設定", style=discord.ButtonStyle.secondary,
                       custom_id="panel:set_voice", row=4)
    async def set_voice(self, interaction, button):
        await self.cog.open_voice(interaction)

    # ラベル・色は __init__ で読み上げ状態に応じて上書きする
    @discord.ui.button(label="読み上げ開始", style=discord.ButtonStyle.success,
                       custom_id="panel:tts_toggle", row=4)
    async def tts_toggle(self, interaction, button):
        await self.cog.toggle_tts(interaction)

    @discord.ui.button(label="説明書", style=discord.ButtonStyle.secondary,
                       custom_id="panel:manual", row=4)
    async def manual(self, interaction, button):
        await self.cog.open_manual(interaction)


class PanelCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.split_team = bot.split_team
        self.panel_messages = {}  # channel_id -> discord.Message

    guild_ids_raw = os.getenv("GUILD_IDS", "")
    GUILD_IDS = [int(gid.strip()) for gid in guild_ids_raw.split(",") if gid]

    @app_commands.command(name="パネル")
    @app_commands.guilds(*[discord.Object(id=guild_id) for guild_id in GUILD_IDS])
    async def show_panel(self, interaction: discord.Interaction):
        # 設置メッセージは出さず、コマンドの応答だけ ephemeral で畳んで消す
        await interaction.response.defer(ephemeral=True)
        await self.repost_panel(interaction.channel)
        await interaction.delete_original_response()

    # 結果メッセージを公開で出し、パネルを最下部へ貼り直す
    async def repost_panel(self, channel):
        old = self.panel_messages.get(channel.id)
        if old is not None:
            try:
                await old.delete()
            except discord.HTTPException:
                pass
        is_reading = self.bot.tts_manager.is_reading(channel.guild.id)
        message = await channel.send(embed=make_panel_embed(), view=PanelView(self, is_reading))
        self.panel_messages[channel.id] = message

    # 読み上げの自動終了など、パネル外の状態変化を既存パネルのボタンへ反映する。
    # 貼り直すと位置が動いて煩わしいので、その場でビューだけ差し替える
    async def refresh_panels(self, guild):
        is_reading = self.bot.tts_manager.is_reading(guild.id)
        for channel_id, message in list(self.panel_messages.items()):
            if message.guild is None or message.guild.id != guild.id:
                continue
            try:
                await message.edit(view=PanelView(self, is_reading))
            except discord.HTTPException:
                pass

    async def post_result(self, channel, embed):
        await self.repost_panel(channel)
        await channel.send(embed=embed)

    # サブ View からの実行後、ephemeral を消して公開結果 + 貼り直し
    async def finish_sub(self, interaction, embed):
        await interaction.response.defer()
        await interaction.delete_original_response()
        await self.post_result(interaction.channel, embed)

    # ---- 各ボタンの入口(押下時に候補を動的生成)----
    async def open_number(self, interaction, mode):
        if not self.split_team.set_member(interaction):
            await interaction.response.send_message(self.split_team.vc_err, ephemeral=True)
            return
        size = self.split_team.memberSize
        values = build_number_values(mode, size)
        label = "チーム数" if mode == "team" else "1チームあたりの人数"
        if not values:
            await interaction.response.send_message("メンバーが少なすぎます", ephemeral=True)
            return
        view = NumberSelectView(self, mode, values)
        await interaction.response.send_message(
            f"{label}を選んでください", view=view, ephemeral=True
        )

    async def open_random(self, interaction):
        if not self.split_team.set_member(interaction):
            await interaction.response.send_message(self.split_team.vc_err, ephemeral=True)
            return
        size = self.split_team.memberSize
        size_values = build_number_values("size", size)
        member_names = [player.name for player in self.split_team.channel_members]
        view = RandomView(self, size_values, member_names)
        await interaction.response.send_message(
            "人数を選び、必要なら優先者を選んで実行してください", view=view, ephemeral=True
        )

    # ランダムピックは VC メンバーに依存しないので VC チェックは不要
    async def open_random_pick(self, interaction):
        view = RandomPickView(self, build_pick_counts())
        await interaction.response.send_message(
            "抽選する数を選んでください", view=view, ephemeral=True
        )

    async def open_register(self, interaction, is_viewer):
        if not self.split_team.set_member(interaction):
            await interaction.response.send_message(self.split_team.vc_err, ephemeral=True)
            return
        member_names = [player.name for player in self.split_team.channel_members]
        if not member_names:
            await interaction.response.send_message("対象がいません", ephemeral=True)
            return
        view = RegisterView(self, is_viewer, member_names)
        label = "観戦者" if is_viewer else "優先者"
        await interaction.response.send_message(
            f"{label}にする対象を選んで実行してください", view=view, ephemeral=True
        )

    async def open_delete(self, interaction, is_viewer):
        self.split_team.set_member(interaction)
        if is_viewer:
            member_names = [p.name for p in self.split_team.channel_members if p.viewer]
        else:
            member_names = [p.name for p in self.split_team.channel_members if p.priority]
        if not member_names:
            await interaction.response.send_message("解除対象がいません", ephemeral=True)
            return
        view = DeleteView(self, is_viewer, member_names)
        label = "観戦者" if is_viewer else "優先者"
        await interaction.response.send_message(
            f"解除する{label}を選んで実行してください(複数可)", view=view, ephemeral=True
        )

    async def open_voice(self, interaction):
        await interaction.response.send_message(
            "読み上げの声を選んでください", view=VoiceSelectView(self), ephemeral=True
        )

    # 意気込みは VC 参加を問わずいつでも編集できるよう、押下で即モーダルを開く
    async def open_aspiration(self, interaction):
        key = (interaction.guild_id, interaction.user.display_name)
        current = self.split_team.aspirations_by_name.get(key, "")
        await interaction.response.send_modal(AspirationModal(self, current))

    # 意気込みを保存する。改行や余分な空白は 1 行に潰し、空なら削除扱いにする
    async def run_set_aspiration(self, interaction, text):
        text = " ".join(text.split())
        self.split_team.set_aspiration(
            interaction.guild_id, interaction.user.id, interaction.user.display_name, text
        )
        message = "意気込みを設定しました。" if text else "意気込みを削除しました。"
        await interaction.response.send_message(message, ephemeral=True)

    # 説明書は押した本人だけに見せ、チャンネルを汚さないよう ephemeral で出す
    async def open_manual(self, interaction):
        await interaction.response.send_message(embed=make_manual_embed(), ephemeral=True)

    # 読み上げ状態に応じて開始・終了を切り替える
    async def toggle_tts(self, interaction):
        if self.bot.tts_manager.is_reading(interaction.guild_id):
            await self.stop_tts(interaction)
        else:
            await self.start_tts(interaction)

    async def start_tts(self, interaction):
        if interaction.user.voice is None:
            await interaction.response.send_message(
                "ボイスチャンネルに入ってから押してください。", ephemeral=True
            )
            return
        # VC 接続に時間がかかっても 3 秒制限で落ちないよう応答を保留する
        await interaction.response.defer()
        try:
            voice_client = await interaction.user.voice.channel.connect()
        except Exception as e:
            # 接続失敗を握りつぶさず、原因の手掛かりとともに通知する
            print(f"❌ VC 接続に失敗: {e}")
            await interaction.followup.send(
                "ボイスチャンネルへの接続に失敗しました。"
                "PyNaCl・ffmpeg が導入されているか確認してください。",
                ephemeral=True,
            )
            return
        self.bot.tts_manager.start_reading(
            interaction.guild_id, interaction.channel_id, voice_client, False
        )
        await self.repost_panel(interaction.channel)

    async def stop_tts(self, interaction):
        await interaction.response.defer()
        await self.bot.tts_manager.stop_reading(interaction.guild_id)
        await self.repost_panel(interaction.channel)

    # 読み上げ声は本人だけの設定なので、公開せず ephemeral で完結させる
    async def run_set_voice(self, interaction, speaker_value, speaker_name):
        self.bot.tts_manager.set_speaker(
            interaction.user.id, speaker_value, interaction.user.display_name
        )
        await interaction.response.edit_message(
            content=f"あなたの声を「{speaker_name}」に設定しました。", view=None
        )

    # ---- 実行本体 ----
    async def run_split(self, interaction, mode, value):
        if mode == "team":
            result = self.split_team.split(interaction, teamCount=value, teamSize=0)
            title = "チーム数で分ける"
        else:
            result = self.split_team.split(interaction, teamCount=0, teamSize=value)
            title = "1チームあたりの人数で分ける"
        await self.finish_sub(interaction, make_split_embed(title, result))

    async def run_random(self, interaction, size, priority_names):
        for name in priority_names:
            self.split_team.add_special(
                interaction, addName=name, isViewer=False, isPriority=True, isRocked=False
            )
        result = self.split_team.split(interaction, teamCount=1, teamSize=size)
        await self.finish_sub(interaction, make_split_embed("人数分抽選", result))

    async def run_random_pick(self, interaction, count):
        # 初回はコールドキャッシュで 3MB DL が入り得るので、取得前に defer して 3 秒制限を回避する
        await interaction.response.defer()
        names = await get_pokemons()
        picks = pick_pokemons(names, count)
        body = "\n".join(
            f"{i}. {to_japanese_name(name)}" for i, name in enumerate(picks, start=1)
        )
        await interaction.delete_original_response()
        await self.post_result(interaction.channel, make_split_embed("ランダムピック", body))

    async def run_register(self, interaction, is_viewer, name, lock):
        result = self.split_team.add_special(
            interaction, addName=name, isViewer=is_viewer, isPriority=not is_viewer, isRocked=lock
        )
        title = "観戦者登録" if is_viewer else "優先者登録"
        await self.finish_sub(interaction, make_special_embed(title, result))

    async def run_delete(self, interaction, is_viewer, names):
        results = [
            self.split_team.delete_special(name, isViewer=is_viewer, isPriority=not is_viewer)
            for name in names
        ]
        title = "観戦者解除" if is_viewer else "優先者解除"
        await self.finish_sub(interaction, make_special_embed(title, "\n".join(results)))

    async def run_show_all(self, interaction):
        self.split_team.set_member(interaction)
        members = self.split_team.channel_members
        rocked_viewers = format_member_list([m for m in members if m.viewer and m.rockedFlag])
        viewers = format_member_list([m for m in members if m.viewer and not m.rockedFlag])
        rocked_priorities = format_member_list([m for m in members if m.priority and m.rockedFlag])
        priorities = format_member_list([m for m in members if m.priority and not m.rockedFlag])
        channel_members = format_member_list([m for m in members if not m.viewer and not m.priority])
        embed = discord.Embed(title="一覧表示", color=discord.Color.red())
        embed.add_field(name="チャンネルメンバー", value=channel_members, inline=False)
        embed.add_field(name="固定観戦者", value=rocked_viewers, inline=False)
        embed.add_field(name="次だけ観戦者", value=viewers, inline=False)
        embed.add_field(name="固定優先者", value=rocked_priorities, inline=False)
        embed.add_field(name="次だけ優先者", value=priorities, inline=False)
        # パネル→出力結果 の順に揃えるため post_result を通す
        await interaction.response.defer()
        await self.post_result(interaction.channel, embed)


async def setup(bot: commands.Bot):
    cog = PanelCog(bot)
    await bot.add_cog(cog)
    bot.add_view(PanelView(cog))  # 再起動後も既存パネルのボタンを有効化
