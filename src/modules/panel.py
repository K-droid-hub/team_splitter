import asyncio
import io

import discord, os
from discord import app_commands
from discord.ext import commands

from src.grouping import format_member_list  # 一覧表示の意気込み整形を共有
from src.modules.tts import SPEAKERS, begin_reading, find_speaker  # 話者一覧と開始処理を共有
from src.pokemons import get_pokemons, pick_pokemons, to_japanese_name

MAX_NUMBER_BUTTONS = 5  # 数値ボタンの最大表示数
MAX_PICK_COUNT = 5  # ランダムピックで一度に選べる最大数(1 チーム分)
MEMBER_BUTTON_LIMIT_SINGLE = 20  # 登録系(単一選択 + 操作ボタン)でボタン表示する上限
MEMBER_BUTTON_LIMIT_MULTI = 15  # 抽選(数値行 + 複数選択 + 実行)でボタン表示する上限
MEMBER_BUTTON_LIMIT_DELETE = 20  # 解除系(複数選択 + 実行)でボタン表示する上限
SELECT_OPTION_LIMIT = 25  # Discord の 1 セレクトあたりの選択肢上限
MAX_SELECT_ROWS = 5  # Discord の 1 View あたりの行数上限
MAX_PANEL_GUILDS = 2  # 同時にパネルを設置できるサーバー数
MAX_PANELS_PER_GUILD = 2  # 1 サーバーあたりでパネルを設置できるチャンネル数


# Discord のセレクトは 1 個 25 件までなので、収まらない分は複数セレクトに分けて並べる。
# 行数にも上限があるため、入り切らなかった件数も返して呼び出し側から伝えられるようにする
def split_into_select_chunks(items, max_selects):
    chunks = [
        items[i : i + SELECT_OPTION_LIMIT]
        for i in range(0, len(items), SELECT_OPTION_LIMIT)
    ][:max_selects]
    shown = sum(len(chunk) for chunk in chunks)
    return chunks, len(items) - shown


def build_number_values(mode, member_size):
    # チーム数は 2 以上、人数は 1 以上で、VC 人数が上限。
    # ボタンに収まらない数の候補は NumberSelectView 側でセレクトに切り替える
    start = 2 if mode == "team" else 1
    return list(range(start, member_size + 1))


# 行数の都合でセレクトに載せ切れなかったメンバーがいるときは、黙らず件数を伝える
def hidden_member_notice(count):
    if not count:
        return ""
    return f"\n(メンバーが多いため、{count} 人は一覧に表示できていません)"


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


def make_voice_embed(current_id, is_default):
    # 自分が今どの声なのか分からないという声があったため、一覧の前に現在の設定を出す。
    # 名前だけでは声質が分からない話者があるので、各行に短い説明を添える
    current = find_speaker(current_id)
    if current is None:
        current_text = f"既定の声(スタイル ID {current_id})"
    elif is_default:
        current_text = f"{current.name}(未設定のため既定の声)"
    else:
        current_text = current.name
    embed = discord.Embed(
        title="🔊 読み上げ声設定",
        description=f"現在のあなたの声: **{current_text}**",
        color=discord.Color.blurple(),
    )
    lines = [
        f"{'✅' if speaker.id == current_id else '・'} **{speaker.name}** {speaker.description}"
        for speaker in SPEAKERS
    ]
    embed.add_field(
        name="声の一覧(🔊試聴で聴き比べできます)", value="\n".join(lines), inline=False
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
            "**読み上げ声設定**: *今のあなたの声を確認し、別の声に変えます(🔊試聴で聴き比べ可)*\n"
            "**読み上げ開始 / 終了**: *パネルのボタンで読み上げを切り替えます*\n"
            "**入退室通知**: *ON にすると入室・退室も読み上げます(チャンネルごとに保持)*\n"
            "**/読み上げ開始**: *入退室通知ありなど詳細指定で開始します*\n"
            "**/読み上げ終了**: *読み上げを終了します*"
        ),
        inline=False,
    )
    embed.add_field(
        name="音楽(!コマンド・VC に入ってから使う)",
        value=(
            "**!play <URL または検索語>**: *VC に入って再生します(再生中ならキューに追加)*\n"
            "**!skip**: *今の曲を飛ばして次へ進みます*\n"
            "**!repeat**: *今の曲をもう一度、次に流します*\n"
            "**!loop**: *今の曲を繰り返します(もう一度押すか !skip・!stop で解除)*\n"
            "**!volume <1〜100>**: *音楽の音量を変えます(省略すると今の音量を表示)*\n"
            "**!queue**: *再生中の曲と順番待ちを表示します*\n"
            "**!stop**: *音楽を終了します*"
        ),
        inline=False,
    )
    embed.add_field(
        name="コマンド",
        value=(
            "**/パネル**: *操作パネルを設置します*\n"
            "**/パネル終了**: *このチャンネルのパネルを閉じます*\n"
            "**/リセット**: *観戦者・優先者の登録を全て解除します*"
        ),
        inline=False,
    )
    return embed


# ---- 時間切れを知らせる View の共通土台 ----
class TimeoutView(discord.ui.View):
    """時間切れ後の操作を黙って失敗させず、ephemeral を差し替えて知らせる View"""

    def __init__(self, timeout=120):
        super().__init__(timeout=timeout)
        self.interaction = None

    # ephemeral は送信元の interaction 経由でしか編集できないので、送信時に覚えておく
    def bind(self, interaction):
        self.interaction = interaction
        return self

    async def on_timeout(self):
        if self.interaction is None:
            return
        try:
            await self.interaction.edit_original_response(
                content="時間切れです。パネルからもう一度操作してください。",
                embed=None,
                view=None,
            )
        except discord.HTTPException:
            pass


# ---- メンバーを複数選ぶセレクトの共通土台 ----
class MemberMultiSelect(discord.ui.Select):
    """メンバーを複数選ぶセレクト。25 件を超える VC では複数個に分けて並べる"""

    PLACEHOLDER = "対象を選択(複数可)"

    def __init__(self, members, row, show_range=False):
        options = [
            discord.SelectOption(label=member.name, value=str(member.user_id))
            for member in members
        ]
        placeholder = self.PLACEHOLDER
        if show_range:
            placeholder += f" ({members[0].name} 〜 {members[-1].name})"
        super().__init__(
            placeholder=placeholder[:150],
            min_values=0,
            max_values=len(options),
            options=options,
            row=row,
        )
        self.names_by_id = {str(member.user_id): member.name for member in members}

    # このセレクトの担当分だけ入れ替える(複数セレクトに分かれても選択を取りこぼさない)
    def apply_selection(self, values):
        selected = self.view.selected_members
        for value in self.names_by_id:
            selected.pop(int(value), None)
        for value in values:
            selected[int(value)] = self.names_by_id[value]

    async def callback(self, interaction):
        self.apply_selection(self.values)
        await interaction.response.defer()


# ---- 数値選択(チーム数 / 人数): 押下即実行 ----
class NumberButton(discord.ui.Button):
    def __init__(self, value, row):
        super().__init__(label=str(value), style=discord.ButtonStyle.primary, row=row)
        self.value = value

    async def callback(self, interaction):
        view = self.view
        await view.cog.run_split(interaction, view.mode, self.value)


class NumberSelect(discord.ui.Select):
    # 候補がボタンに収まらない大人数 VC 用のフォールバック
    def __init__(self, values, row, placeholder):
        options = [discord.SelectOption(label=str(value)) for value in values]
        super().__init__(
            placeholder=placeholder, min_values=1, max_values=1, options=options, row=row
        )

    async def callback(self, interaction):
        view = self.view
        await view.cog.run_split(interaction, view.mode, int(self.values[0]))


class NumberSelectView(TimeoutView):
    def __init__(self, cog, mode, values, label="値"):
        super().__init__()
        self.cog = cog
        self.mode = mode  # "team" or "size"
        if len(values) <= MAX_NUMBER_BUTTONS:
            for value in values:
                self.add_item(NumberButton(value, row=0))
            return
        # ボタンに収まらない候補はセレクトへ切り替え、大人数 VC でも全部選べるようにする
        chunks, _ = split_into_select_chunks(values, MAX_SELECT_ROWS)
        for row, chunk in enumerate(chunks):
            placeholder = f"{label}を選択"
            if len(chunks) > 1:
                placeholder += f" ({chunk[0]}〜{chunk[-1]})"
            self.add_item(NumberSelect(chunk, row, placeholder))


# ---- ランダムピック: 抽選数を押下即実行 ----
class PickCountButton(discord.ui.Button):
    def __init__(self, value, row):
        super().__init__(label=str(value), style=discord.ButtonStyle.primary, row=row)
        self.value = value

    async def callback(self, interaction):
        await self.view.cog.run_random_pick(interaction, self.value)


class RandomPickView(TimeoutView):
    def __init__(self, cog, counts):
        super().__init__()
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


class SizeSelect(discord.ui.Select):
    # 人数の候補がボタンに収まらない場合のフォールバック
    def __init__(self, values, row):
        options = [discord.SelectOption(label=str(value)) for value in values]
        super().__init__(
            placeholder="人数を選択", min_values=1, max_values=1, options=options, row=row
        )

    async def callback(self, interaction):
        self.view.selected_size = int(self.values[0])
        await interaction.response.edit_message(view=self.view)


class PriorityToggleButton(discord.ui.Button):
    def __init__(self, member, row):
        super().__init__(label=member.name, style=discord.ButtonStyle.secondary, row=row)
        self.user_id = member.user_id
        self.member_name = member.name

    async def callback(self, interaction):
        view = self.view
        if self.user_id in view.selected_members:
            del view.selected_members[self.user_id]
            self.style = discord.ButtonStyle.secondary
        else:
            view.selected_members[self.user_id] = self.member_name
            self.style = discord.ButtonStyle.success
        await interaction.response.edit_message(view=view)


class PriorityMultiSelect(MemberMultiSelect):
    # メンバーが多くボタンで収まらない場合のフォールバック
    PLACEHOLDER = "優先者を選択(任意・複数可)"


class RandomExecuteButton(discord.ui.Button):
    def __init__(self, row):
        super().__init__(label="実行", style=discord.ButtonStyle.primary, row=row)

    async def callback(self, interaction):
        view = self.view
        if view.selected_size is None:
            await interaction.response.send_message("人数を選んでください", ephemeral=True)
            return
        await view.cog.run_random(interaction, view.selected_size, list(view.selected_members))


class RandomView(TimeoutView):
    def __init__(self, cog, size_values, members):
        super().__init__()
        self.cog = cog
        self.selected_size = None
        self.selected_members = {}  # user_id -> 表示名
        self.hidden_member_count = 0
        if len(size_values) <= MAX_NUMBER_BUTTONS:
            for value in size_values:
                self.add_item(SizeButton(value, row=0))
        else:
            self.add_item(SizeSelect(size_values[:SELECT_OPTION_LIMIT], row=0))
        if len(members) <= MEMBER_BUTTON_LIMIT_MULTI:
            for i, member in enumerate(members):
                self.add_item(PriorityToggleButton(member, row=1 + i // 5))
        else:
            # 実行ボタン用に 1 行残すので、セレクトは最大 3 行まで
            chunks, self.hidden_member_count = split_into_select_chunks(
                members, MAX_SELECT_ROWS - 2
            )
            for index, chunk in enumerate(chunks):
                self.add_item(PriorityMultiSelect(chunk, 1 + index, len(chunks) > 1))
        self.add_item(RandomExecuteButton(row=4))


# ---- 観戦者 / 優先者 登録: 対象(単一) + ロック + 実行 ----
class RegisterMemberButton(discord.ui.Button):
    def __init__(self, member, row):
        super().__init__(label=member.name, style=discord.ButtonStyle.secondary, row=row)
        self.user_id = member.user_id
        self.member_name = member.name

    async def callback(self, interaction):
        view = self.view
        view.select_member(self.user_id, self.member_name)
        for item in view.children:
            if isinstance(item, RegisterMemberButton):
                item.style = (
                    discord.ButtonStyle.success
                    if item.user_id == self.user_id
                    else discord.ButtonStyle.secondary
                )
        await interaction.response.edit_message(view=view)


class RegisterMemberSelect(discord.ui.Select):
    def __init__(self, members, row, show_range):
        options = [
            discord.SelectOption(label=member.name, value=str(member.user_id))
            for member in members
        ]
        placeholder = "対象を選択"
        if show_range:
            placeholder += f" ({members[0].name} 〜 {members[-1].name})"
        super().__init__(
            placeholder=placeholder[:150],
            min_values=0,
            max_values=1,
            options=options,
            row=row,
        )
        self.names_by_id = {str(member.user_id): member.name for member in members}

    async def callback(self, interaction):
        view = self.view
        if self.values:
            view.select_member(int(self.values[0]), self.names_by_id[self.values[0]])
        await interaction.response.edit_message(view=view)


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
        if view.selected_user_id is None:
            await interaction.response.send_message("対象を選んでください", ephemeral=True)
            return
        await view.cog.run_register(
            interaction, view.is_viewer, view.selected_name, view.selected_user_id, view.lock
        )


class RegisterView(TimeoutView):
    def __init__(self, cog, is_viewer, members):
        super().__init__()
        self.cog = cog
        self.is_viewer = is_viewer
        self.selected_name = None
        self.selected_user_id = None
        self.lock = False
        self.hidden_member_count = 0
        if len(members) <= MEMBER_BUTTON_LIMIT_SINGLE:
            for i, member in enumerate(members):
                self.add_item(RegisterMemberButton(member, row=i // 5))
        else:
            # ロック・実行ボタン用に 1 行残すので、セレクトは最大 4 行まで
            chunks, self.hidden_member_count = split_into_select_chunks(
                members, MAX_SELECT_ROWS - 1
            )
            for index, chunk in enumerate(chunks):
                self.add_item(RegisterMemberSelect(chunk, index, len(chunks) > 1))
        self.execute_button = RegisterExecuteButton(row=4)
        self.add_item(LockToggleButton(row=4))
        self.add_item(self.execute_button)

    # 選択中の対象を実行ボタンのラベルにも出す(セレクトが複数行でも迷わないように)
    def select_member(self, user_id, name):
        self.selected_user_id = user_id
        self.selected_name = name
        self.execute_button.label = f"実行 ({name})"[:80]


# ---- 観戦者 / 優先者 解除: 対象(複数) + 実行 ----
class DeleteToggleButton(discord.ui.Button):
    def __init__(self, member, row):
        super().__init__(label=member.name, style=discord.ButtonStyle.secondary, row=row)
        self.user_id = member.user_id
        self.member_name = member.name

    async def callback(self, interaction):
        view = self.view
        if self.user_id in view.selected_members:
            del view.selected_members[self.user_id]
            self.style = discord.ButtonStyle.secondary
        else:
            view.selected_members[self.user_id] = self.member_name
            self.style = discord.ButtonStyle.success
        await interaction.response.edit_message(view=view)


class DeleteMultiSelect(MemberMultiSelect):
    # メンバーが多くボタンで収まらない場合のフォールバック
    PLACEHOLDER = "解除する対象を選択(複数可)"


class DeleteExecuteButton(discord.ui.Button):
    def __init__(self, row):
        super().__init__(label="実行", style=discord.ButtonStyle.primary, row=row)

    async def callback(self, interaction):
        view = self.view
        if not view.selected_members:
            await interaction.response.send_message("対象を選んでください", ephemeral=True)
            return
        await view.cog.run_delete(interaction, view.is_viewer, dict(view.selected_members))


class DeleteView(TimeoutView):
    def __init__(self, cog, is_viewer, members):
        super().__init__()
        self.cog = cog
        self.is_viewer = is_viewer
        self.selected_members = {}  # user_id -> 表示名
        self.hidden_member_count = 0
        if len(members) <= MEMBER_BUTTON_LIMIT_DELETE:
            for i, member in enumerate(members):
                self.add_item(DeleteToggleButton(member, row=i // 5))
        else:
            # 実行ボタン用に 1 行残すので、セレクトは最大 4 行まで
            chunks, self.hidden_member_count = split_into_select_chunks(
                members, MAX_SELECT_ROWS - 1
            )
            for index, chunk in enumerate(chunks):
                self.add_item(DeleteMultiSelect(chunk, index, len(chunks) > 1))
        self.add_item(DeleteExecuteButton(row=4))


# ---- 読み上げ声設定: 話者ごとに「設定 + 試聴」----
class VoiceButton(discord.ui.Button):
    def __init__(self, speaker_name, speaker_value, row, is_current=False):
        # 今の声だけ緑にして、ボタンの並びからも自分の設定が分かるようにする
        style = discord.ButtonStyle.success if is_current else discord.ButtonStyle.primary
        super().__init__(label=speaker_name, style=style, row=row)
        self.speaker_name = speaker_name
        self.speaker_value = speaker_value

    async def callback(self, interaction):
        await self.view.cog.run_set_voice(interaction, self.speaker_value, self.speaker_name)


class VoicePreviewButton(discord.ui.Button):
    def __init__(self, speaker_name, speaker_value, row):
        super().__init__(label="🔊試聴", style=discord.ButtonStyle.secondary, row=row)
        self.speaker_name = speaker_name
        self.speaker_value = speaker_value

    async def callback(self, interaction):
        await self.view.cog.run_preview_voice(interaction, self.speaker_value, self.speaker_name)


class VoiceSelectView(TimeoutView):
    def __init__(self, cog, current_id=None):
        super().__init__()
        self.cog = cog
        # 各話者を「話者ボタン(押下即設定)+ 試聴ボタン」の 2 つ 1 組で並べる。
        # 1 行に 2 話者(4 ボタン)ずつ置き、Discord の 5 行制限内(最大 10 話者)に収める
        for index, speaker in enumerate(SPEAKERS):
            row = index // 2
            self.add_item(
                VoiceButton(speaker.name, speaker.id, row, speaker.id == current_id)
            )
            self.add_item(VoicePreviewButton(speaker.name, speaker.id, row))


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
    def __init__(self, cog, is_reading=False, notice_on=False):
        super().__init__(timeout=None)
        self.cog = cog
        # 読み上げ状態に応じて 1 つのトグルボタンの見た目を切り替える
        if is_reading:
            self.tts_toggle.label = "読み上げ終了"
            self.tts_toggle.style = discord.ButtonStyle.danger
        else:
            self.tts_toggle.label = "読み上げ開始"
            self.tts_toggle.style = discord.ButtonStyle.success
        # 入退室通知は ON のときだけ緑にして、今の設定が一目で分かるようにする
        self.notice_toggle.label = f"入退室通知: {'ON' if notice_on else 'OFF'}"
        self.notice_toggle.style = (
            discord.ButtonStyle.success if notice_on else discord.ButtonStyle.secondary
        )

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

    # 宣言順がそのまま行内の並び順になる。観戦者 → 優先者で、登録と解除を対にして並べる
    @discord.ui.button(label="観戦者登録", style=discord.ButtonStyle.secondary,
                       custom_id="panel:add_viewer", row=1)
    async def add_viewer(self, interaction, button):
        await self.cog.open_register(interaction, is_viewer=True)

    @discord.ui.button(label="観戦者解除", style=discord.ButtonStyle.secondary,
                       custom_id="panel:del_viewer", row=1)
    async def del_viewer(self, interaction, button):
        await self.cog.open_delete(interaction, is_viewer=True)

    @discord.ui.button(label="優先者登録", style=discord.ButtonStyle.secondary,
                       custom_id="panel:add_priority", row=1)
    async def add_priority(self, interaction, button):
        await self.cog.open_register(interaction, is_viewer=False)

    @discord.ui.button(label="優先者解除", style=discord.ButtonStyle.secondary,
                       custom_id="panel:del_priority", row=1)
    async def del_priority(self, interaction, button):
        await self.cog.open_delete(interaction, is_viewer=False)

    # 参照と自分向けの設定。緑は読み上げトグルだけに使うため、ここは全て secondary
    @discord.ui.button(label="一覧表示", style=discord.ButtonStyle.secondary,
                       custom_id="panel:show_all", row=2)
    async def show_all(self, interaction, button):
        await self.cog.run_show_all(interaction)

    @discord.ui.button(label="意気込み設定", style=discord.ButtonStyle.secondary,
                       custom_id="panel:aspiration", row=2)
    async def aspiration(self, interaction, button):
        await self.cog.open_aspiration(interaction)

    @discord.ui.button(label="読み上げ声設定", style=discord.ButtonStyle.secondary,
                       custom_id="panel:set_voice", row=2)
    async def set_voice(self, interaction, button):
        await self.cog.open_voice(interaction)

    # ラベル・色は __init__ で読み上げ状態に応じて上書きする
    @discord.ui.button(label="読み上げ開始", style=discord.ButtonStyle.success,
                       custom_id="panel:tts_toggle", row=3)
    async def tts_toggle(self, interaction, button):
        await self.cog.toggle_tts(interaction)

    @discord.ui.button(label="入退室通知", style=discord.ButtonStyle.secondary,
                       custom_id="panel:notice_toggle", row=3)
    async def notice_toggle(self, interaction, button):
        await self.cog.toggle_join_leave_notice(interaction)

    @discord.ui.button(label="説明書", style=discord.ButtonStyle.secondary,
                       custom_id="panel:manual", row=3)
    async def manual(self, interaction, button):
        await self.cog.open_manual(interaction)


class PanelCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.split_team = bot.split_team
        self.panel_store = bot.panel_store
        self.panel_messages = {}  # channel_id -> discord.Message
        # 貼り直しは delete→send の 2 段なので、同時操作で 2 枚貼られないよう
        # チャンネル単位で直列化する
        self.panel_locks = {}  # channel_id -> asyncio.Lock

    guild_ids_raw = os.getenv("GUILD_IDS", "")
    GUILD_IDS = [int(gid.strip()) for gid in guild_ids_raw.split(",") if gid]

    # 同時に設置できるパネルを「2 サーバー × 各 2 チャンネル」に制限する。
    # 上限超過は黙って弾かず、今どこで開いているかと閉じ方を返す
    def check_panel_capacity(self, channel):
        locations = self.panel_store.load_panels()
        if channel.id in locations:
            return None  # 既にこのチャンネルにあるので、貼り直しは常に許可する
        guild_id = channel.guild.id
        same_guild = [cid for cid, (gid, _) in locations.items() if gid == guild_id]
        if len(same_guild) >= MAX_PANELS_PER_GUILD:
            opened = "・".join(f"<#{cid}>" for cid in same_guild)
            return (
                f"このサーバーでは既に {MAX_PANELS_PER_GUILD} チャンネルでパネルを開いています"
                f"({opened})。どちらかで `/パネル終了` を実行してから設置してください。"
            )
        guild_ids = {gid for gid, _ in locations.values()}
        # 既にパネルを置いているサーバーなら、サーバー数は増えないので通す
        if guild_id not in guild_ids and len(guild_ids) >= MAX_PANEL_GUILDS:
            return (
                f"同時にパネルを置けるのは {MAX_PANEL_GUILDS} サーバーまでです。"
                "他のサーバーで `/パネル終了` を実行してから設置してください。"
            )
        return None

    @app_commands.command(name="パネル")
    @app_commands.guilds(*[discord.Object(id=guild_id) for guild_id in GUILD_IDS])
    async def show_panel(self, interaction: discord.Interaction):
        error = self.check_panel_capacity(interaction.channel)
        if error is not None:
            await interaction.response.send_message(error, ephemeral=True)
            return
        # 設置メッセージは出さず、コマンドの応答だけ ephemeral で畳んで消す
        await interaction.response.defer(ephemeral=True)
        await self.repost_panel(interaction.channel)
        await interaction.delete_original_response()

    # 上限に達したときに古いパネルを閉じられる手段。設置数の管理もこれで完結する
    @app_commands.command(name="パネル終了")
    @app_commands.guilds(*[discord.Object(id=guild_id) for guild_id in GUILD_IDS])
    async def close_panel(self, interaction: discord.Interaction):
        channel = interaction.channel
        if channel.id not in self.panel_store.load_panels():
            await interaction.response.send_message(
                "このチャンネルにはパネルがありません。", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        await self.delete_panel_message(channel)
        self.panel_store.delete_panel(channel.id)
        self.panel_locks.pop(channel.id, None)
        await interaction.followup.send("パネルを閉じました。", ephemeral=True)

    # 古いパネルを消す。再起動で panel_messages を失っていても、保存した
    # message_id から取り直して消す(消し漏れでパネルが増殖しないように)
    async def delete_panel_message(self, channel):
        message = self.panel_messages.pop(channel.id, None)
        if message is None:
            stored = self.panel_store.load_panels().get(channel.id)
            if stored is None or stored[1] is None:
                return
            try:
                message = await channel.fetch_message(stored[1])
            except discord.HTTPException:
                return
        try:
            await message.delete()
        except discord.HTTPException:
            pass

    # 結果メッセージを公開で出し、パネルを最下部へ貼り直す
    async def repost_panel(self, channel):
        lock = self.panel_locks.setdefault(channel.id, asyncio.Lock())
        async with lock:
            await self.delete_panel_message(channel)
            # ラベルはギルド全体ではなく、このチャンネルが読み上げ対象かで決める
            message = await channel.send(
                embed=make_panel_embed(), view=self.build_view(channel), silent=True
            )
            self.panel_messages[channel.id] = message
            self.panel_store.set_panel(channel.guild.id, channel.id, message.id)

    # 読み上げの自動終了など、パネル外の状態変化を既存パネルのボタンへ反映する。
    # 貼り直すと位置が動いて煩わしいので、その場でビューだけ差し替える
    async def refresh_panels(self, guild):
        for channel_id, message in list(self.panel_messages.items()):
            if message.guild is None or message.guild.id != guild.id:
                continue
            try:
                await message.edit(view=self.build_view(message.channel))
            except discord.HTTPException:
                pass

    async def post_result(self, channel, embed):
        await self.repost_panel(channel)
        await channel.send(embed=embed, silent=True)

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
        view = NumberSelectView(self, mode, values, label)
        await interaction.response.send_message(
            f"{label}を選んでください", view=view.bind(interaction), ephemeral=True
        )

    async def open_random(self, interaction):
        if not self.split_team.set_member(interaction):
            await interaction.response.send_message(self.split_team.vc_err, ephemeral=True)
            return
        size = self.split_team.memberSize
        size_values = build_number_values("size", size)
        members = list(self.split_team.channel_members)
        view = RandomView(self, size_values, members)
        text = "人数を選び、必要なら優先者を選んで実行してください"
        await interaction.response.send_message(
            text + hidden_member_notice(view.hidden_member_count),
            view=view.bind(interaction),
            ephemeral=True,
        )

    # ランダムピックは VC メンバーに依存しないので VC チェックは不要
    async def open_random_pick(self, interaction):
        view = RandomPickView(self, build_pick_counts()).bind(interaction)
        await interaction.response.send_message(
            "抽選する数を選んでください", view=view, ephemeral=True
        )

    async def open_register(self, interaction, is_viewer):
        if not self.split_team.set_member(interaction):
            await interaction.response.send_message(self.split_team.vc_err, ephemeral=True)
            return
        members = list(self.split_team.channel_members)
        if not members:
            await interaction.response.send_message("対象がいません", ephemeral=True)
            return
        view = RegisterView(self, is_viewer, members)
        label = "観戦者" if is_viewer else "優先者"
        await interaction.response.send_message(
            f"{label}にする対象を選んで実行してください"
            + hidden_member_notice(view.hidden_member_count),
            view=view.bind(interaction),
            ephemeral=True,
        )

    async def open_delete(self, interaction, is_viewer):
        # 解除対象は今の VC の登録状況から出すので、ここでも VC チェックを通す
        if not self.split_team.set_member(interaction):
            await interaction.response.send_message(self.split_team.vc_err, ephemeral=True)
            return
        if is_viewer:
            members = [p for p in self.split_team.channel_members if p.viewer]
        else:
            members = [p for p in self.split_team.channel_members if p.priority]
        if not members:
            await interaction.response.send_message("解除対象がいません", ephemeral=True)
            return
        view = DeleteView(self, is_viewer, members)
        label = "観戦者" if is_viewer else "優先者"
        await interaction.response.send_message(
            f"解除する{label}を選んで実行してください(複数可)"
            + hidden_member_notice(view.hidden_member_count),
            view=view.bind(interaction),
            ephemeral=True,
        )

    # 現在の声の表示は設定直後にも貼り替えるので、embed と View の組み立てをまとめる
    def build_voice_message(self, user_id):
        manager = self.bot.tts_manager
        current_id = manager.get_speaker(user_id)
        embed = make_voice_embed(current_id, not manager.has_speaker(user_id))
        return embed, VoiceSelectView(self, current_id)

    async def open_voice(self, interaction):
        embed, view = self.build_voice_message(interaction.user.id)
        await interaction.response.send_message(
            embed=embed, view=view.bind(interaction), ephemeral=True
        )

    # 意気込みは VC 参加を問わずいつでも編集できるよう、押下で即モーダルを開く
    async def open_aspiration(self, interaction):
        key = (interaction.guild_id, interaction.user.id)
        current = self.split_team.aspirations_by_user.get(key, "")
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
    # パネルは貼り直さず、押されたボタンの表示だけ差し替える
    async def toggle_join_leave_notice(self, interaction):
        manager = self.bot.tts_manager
        enabled = not manager.join_leave_notice(interaction.channel_id)
        manager.set_join_leave_notice(interaction.channel_id, enabled)
        await interaction.response.edit_message(view=self.build_view(interaction.channel))

    # ボタンの表示は、そのチャンネルの読み上げ状態と入退室通知の設定から決める
    def build_view(self, channel):
        manager = self.bot.tts_manager
        return PanelView(
            self,
            is_reading=manager.is_target_channel(channel.guild.id, channel.id),
            notice_on=manager.join_leave_notice(channel.id),
        )

    async def toggle_tts(self, interaction):
        # このチャンネルが読み上げ対象のときだけ「終了」、それ以外は「開始」
        if self.bot.tts_manager.is_target_channel(
            interaction.guild_id, interaction.channel_id
        ):
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
        # 別のボイスチャンネルなら、他のテキストチャンネルが読み上げ中でも並行して始められる。
        # 断る条件(同じボイスチャンネルで読み上げ中・空き不足)は /読み上げ開始 と共通
        # 入退室通知は、パネルのボタンで設定したチャンネルごとの値をそのまま使う
        error = await begin_reading(
            self.bot,
            interaction.channel_id,
            interaction.user.voice.channel,
            self.bot.tts_manager.join_leave_notice(interaction.channel_id),
        )
        if error is not None:
            await interaction.followup.send(error, ephemeral=True)
            return
        await self.repost_panel(interaction.channel)

    async def stop_tts(self, interaction):
        await interaction.response.defer()
        await self.bot.tts_manager.stop_reading(interaction.channel_id)
        await self.repost_panel(interaction.channel)

    # 読み上げ声は本人だけの設定なので、公開せず ephemeral で完結させる
    async def run_set_voice(self, interaction, speaker_value, speaker_name):
        self.bot.tts_manager.set_speaker(
            interaction.user.id, speaker_value, interaction.user.display_name
        )
        # 設定後も同じ ephemeral を最新状態で貼り替え、そのまま聴き比べ・選び直しができる
        embed, view = self.build_voice_message(interaction.user.id)
        await interaction.response.edit_message(
            content=f"あなたの声を「{speaker_name}」に設定しました。", embed=embed, view=view
        )

    # 選択中の話者でサンプル音声を合成し、本人だけに聞こえる ephemeral 添付で試聴させる。
    # 設定は変えず、声設定 View も残したまま何度でも聴き比べできるようにする
    async def run_preview_voice(self, interaction, speaker_value, speaker_name):
        # 合成に時間がかかっても 3 秒制限で落ちないよう、先に応答を保留する
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            wav = await self.bot.tts_manager.synthesize_sample(speaker_value)
        except Exception as e:
            # 合成失敗を握りつぶさず、原因の手掛かりとともに通知する
            print(f"❌ 試聴音声の合成に失敗: {e}")
            await interaction.followup.send(
                "試聴音声の生成に失敗しました。VOICEVOX エンジンが起動しているか確認してください。",
                ephemeral=True,
            )
            return
        sample_file = discord.File(io.BytesIO(wav), filename="sample.wav")
        await interaction.followup.send(
            content=f"「{speaker_name}」の試聴音声です。", file=sample_file, ephemeral=True
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

    async def run_random(self, interaction, size, priority_user_ids):
        for user_id in priority_user_ids:
            self.split_team.add_special(
                interaction,
                addName=None,
                isViewer=False,
                isPriority=True,
                isRocked=False,
                user_id=user_id,
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

    async def run_register(self, interaction, is_viewer, name, user_id, lock):
        result = self.split_team.add_special(
            interaction,
            addName=name,
            isViewer=is_viewer,
            isPriority=not is_viewer,
            isRocked=lock,
            user_id=user_id,
        )
        title = "観戦者登録" if is_viewer else "優先者登録"
        await self.finish_sub(interaction, make_special_embed(title, result))

    async def run_delete(self, interaction, is_viewer, members):
        results = [
            self.split_team.delete_special(
                interaction,
                name,
                isViewer=is_viewer,
                isPriority=not is_viewer,
                user_id=user_id,
            )
            for user_id, name in members.items()
        ]
        title = "観戦者解除" if is_viewer else "優先者解除"
        await self.finish_sub(interaction, make_special_embed(title, "\n".join(results)))

    async def run_show_all(self, interaction):
        # VC チェックを省くと、前回別の VC で作ったリストが公開チャンネルに出てしまう
        if not self.split_team.set_member(interaction):
            await interaction.response.send_message(self.split_team.vc_err, ephemeral=True)
            return
        members = self.split_team.channel_members
        sections = [
            ("チャンネルメンバー", [m for m in members if not m.viewer and not m.priority]),
            ("固定観戦者", [m for m in members if m.viewer and m.rockedFlag]),
            ("次だけ観戦者", [m for m in members if m.viewer and not m.rockedFlag]),
            ("固定優先者", [m for m in members if m.priority and m.rockedFlag]),
            ("次だけ優先者", [m for m in members if m.priority and not m.rockedFlag]),
        ]
        embed = discord.Embed(title="一覧表示", color=discord.Color.blue())
        # 「なし」が並ぶと読みにくいので、該当者がいるセクションだけ出す
        for name, section_members in sections:
            if section_members:
                embed.add_field(
                    name=name, value=format_member_list(section_members), inline=False
                )
        if not embed.fields:
            embed.description = "表示できるメンバーがいません。"
        # パネル→出力結果 の順に揃えるため post_result を通す
        await interaction.response.defer()
        await self.post_result(interaction.channel, embed)


async def setup(bot: commands.Bot):
    cog = PanelCog(bot)
    await bot.add_cog(cog)
    bot.add_view(PanelView(cog))  # 再起動後も既存パネルのボタンを有効化
