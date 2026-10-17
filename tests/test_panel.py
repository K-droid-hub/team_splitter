import asyncio
import os
from unittest.mock import AsyncMock, Mock

os.environ.setdefault("GUILD_IDS", "0")  # panel の import 時に GUILD_IDS を参照するため

from types import SimpleNamespace

import discord

from src.modules.panel import (
    MAX_PANELS_PER_GUILD,
    MAX_PANEL_GUILDS,
    MEMBER_BUTTON_LIMIT_DELETE,
    MEMBER_BUTTON_LIMIT_MULTI,
    MEMBER_BUTTON_LIMIT_SINGLE,
    DeleteMultiSelect,
    DeleteView,
    NumberSelectView,
    PanelCog,
    PanelView,
    RandomView,
    RegisterView,
    VoiceButton,
    VoicePreviewButton,
    VoiceSelectView,
    build_number_values,
    build_pick_counts,
    make_voice_embed,
)
from src.panel_store import PanelStore
from src.modules.tts import SPEAKERS


def test_build_number_values_team_starts_at_two():
    # チーム数は 1 チームを許さず 2 から始まる
    assert build_number_values("team", 4) == [2, 3, 4]


def test_build_number_values_size_starts_at_one():
    assert build_number_values("size", 4) == [1, 2, 3, 4]


def test_build_number_values_not_capped_for_large_vc():
    # 大人数 VC でも候補を切らない(収まらない分は表示側がセレクトに切り替える)
    assert build_number_values("team", 8) == [2, 3, 4, 5, 6, 7, 8]
    assert build_number_values("size", 8) == [1, 2, 3, 4, 5, 6, 7, 8]


def test_build_number_values_too_few_members_returns_empty():
    # 1 人しかいなければチーム分割の候補は出ない
    assert build_number_values("team", 1) == []


def test_build_pick_counts_is_one_to_five():
    # ランダムピックの抽選数は 1〜5 の固定値
    assert build_pick_counts() == [1, 2, 3, 4, 5]


# ---- 読み上げ声設定 View: 試聴ボタン併設 ----

def make_cog():
    # PanelCog は bot.split_team / bot.tts_manager を参照するので最小限用意する
    bot = Mock()
    bot.split_team = Mock()
    bot.tts_manager = Mock()
    # パネル位置はファイルを作らないインメモリ DB で持つ
    bot.panel_store = PanelStore(db_path=":memory:")
    return PanelCog(bot)


def test_voice_select_view_pairs_speaker_with_preview():
    async def scenario():
        view = VoiceSelectView(make_cog())
        speakers = view.children[0::2]
        previews = view.children[1::2]
        # 各話者が「話者ボタン + 試聴ボタン」の 2 つを持つこと
        assert len(view.children) == len(SPEAKERS) * 2
        assert all(isinstance(b, VoiceButton) for b in speakers)
        assert all(isinstance(b, VoicePreviewButton) for b in previews)
        for index, speaker in enumerate(SPEAKERS):
            # 話者と試聴が同じ行に隣接し、同じ話者を指すこと
            assert speakers[index].label == speaker.name
            assert speakers[index].speaker_value == speaker.id
            assert speakers[index].row == previews[index].row
            # 1 行に 2 話者ずつ並べること
            assert speakers[index].row == index // 2

    asyncio.run(scenario())


def test_voice_select_view_fits_discord_row_limit():
    async def scenario():
        view = VoiceSelectView(make_cog())
        # Discord の制限(5 行・1 行 5 ボタン)を超えないこと
        assert max(button.row for button in view.children) <= 4
        buttons_per_row = {}
        for button in view.children:
            buttons_per_row[button.row] = buttons_per_row.get(button.row, 0) + 1
        assert all(count <= 5 for count in buttons_per_row.values())

    asyncio.run(scenario())


def test_voice_select_view_highlights_current_speaker():
    async def scenario():
        view = VoiceSelectView(make_cog(), current_id=9)
        for button in view.children[0::2]:
            # 今の声だけ緑、それ以外は青にすること
            expected = (
                discord.ButtonStyle.success
                if button.speaker_value == 9
                else discord.ButtonStyle.primary
            )
            assert button.style == expected

    asyncio.run(scenario())


def test_voice_embed_marks_current_speaker():
    embed = make_voice_embed(9, False)
    # 今の声が一目で分かるよう、説明文とチェックマークの両方で示すこと
    assert "波音リツ" in embed.description
    assert "未設定" not in embed.description
    lines = embed.fields[0].value.split("\n")
    assert [line for line in lines if line.startswith("✅")] == [
        "✅ **波音リツ** 低めでクールな女性声"
    ]
    # 名前だけでは分からない声のために、全話者に説明が付くこと
    assert len(lines) == len(SPEAKERS)
    assert all(speaker.description in embed.fields[0].value for speaker in SPEAKERS)


def test_voice_embed_notes_default_when_unset():
    embed = make_voice_embed(3, True)
    # 自分で選んでいない人には、既定の声であることを明示すること
    assert "ずんだもん" in embed.description
    assert "未設定" in embed.description


def test_voice_embed_handles_speaker_outside_list():
    # VOICEVOX_SPEAKER に一覧外のスタイル ID を指定していても落ちないこと
    embed = make_voice_embed(99, True)
    assert "99" in embed.description


def test_speaker_names_are_unique_and_without_emotion_labels():
    names = [speaker.name for speaker in SPEAKERS]
    # ボタンが同名で並ぶと見分けられないため、表示名は重複しないこと
    assert len(set(names)) == len(names)
    # 感情名(喜び 等)は表示名に出さず、説明側で伝えること
    assert not any("喜び" in name or "熱血" in name for name in names)


def test_run_preview_voice_defers_and_sends_sample_file():
    async def scenario():
        cog = make_cog()
        cog.bot.tts_manager.synthesize_sample = AsyncMock(return_value=b"WAV")
        interaction = Mock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        await cog.run_preview_voice(interaction, speaker_value=8, speaker_name="春日部つむぎ")

        # 3 秒制限を避けるため先に ephemeral で defer すること
        interaction.response.defer.assert_awaited_once()
        assert interaction.response.defer.call_args.kwargs["ephemeral"] is True
        cog.bot.tts_manager.synthesize_sample.assert_awaited_once_with(8)
        # 合成した WAV を ephemeral 添付で送ること
        send_kwargs = interaction.followup.send.call_args.kwargs
        assert send_kwargs["ephemeral"] is True
        assert send_kwargs["file"].filename == "sample.wav"

    asyncio.run(scenario())


def test_run_preview_voice_reports_synthesis_failure():
    async def scenario():
        cog = make_cog()
        cog.bot.tts_manager.synthesize_sample = AsyncMock(side_effect=RuntimeError("boom"))
        interaction = Mock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        await cog.run_preview_voice(interaction, speaker_value=3, speaker_name="ずんだもん")

        # 合成失敗を握りつぶさず、ファイルなしのエラーメッセージを ephemeral で返すこと
        send_kwargs = interaction.followup.send.call_args.kwargs
        assert "file" not in send_kwargs
        assert send_kwargs["ephemeral"] is True

    asyncio.run(scenario())


# ---- パネルのラベルはギルド全体ではなくチャンネル単位で決まる ----

def make_channel(channel_id, guild_id, message_id=900):
    channel = Mock()
    channel.id = channel_id
    channel.guild.id = guild_id
    channel.send = AsyncMock(return_value=Mock(id=message_id))
    return channel


def test_repost_panel_shows_start_on_non_reading_channel():
    # 別チャンネルで読み上げ中でも、非対象チャンネルのボタンは「読み上げ開始」
    async def scenario():
        cog = make_cog()
        cog.bot.tts_manager.is_target_channel = Mock(return_value=False)
        channel = make_channel(channel_id=100, guild_id=1)

        await cog.repost_panel(channel)

        cog.bot.tts_manager.is_target_channel.assert_called_once_with(1, 100)
        view = channel.send.call_args.kwargs["view"]
        assert view.tts_toggle.label == "読み上げ開始"

    asyncio.run(scenario())


def test_repost_panel_shows_stop_on_reading_channel():
    # 読み上げ対象チャンネルのボタンだけが「読み上げ終了」
    async def scenario():
        cog = make_cog()
        cog.bot.tts_manager.is_target_channel = Mock(return_value=True)
        channel = make_channel(channel_id=100, guild_id=1)

        await cog.repost_panel(channel)

        view = channel.send.call_args.kwargs["view"]
        assert view.tts_toggle.label == "読み上げ終了"

    asyncio.run(scenario())


def test_toggle_tts_stops_only_on_target_channel():
    async def scenario():
        cog = make_cog()
        cog.stop_tts = AsyncMock()
        cog.start_tts = AsyncMock()
        interaction = Mock()
        interaction.guild_id = 1
        interaction.channel_id = 100

        # 対象チャンネルなら終了へ
        cog.bot.tts_manager.is_target_channel = Mock(return_value=True)
        await cog.toggle_tts(interaction)
        cog.stop_tts.assert_awaited_once()
        cog.start_tts.assert_not_awaited()

        # 非対象チャンネルなら開始へ
        cog.stop_tts.reset_mock()
        cog.bot.tts_manager.is_target_channel = Mock(return_value=False)
        await cog.toggle_tts(interaction)
        cog.start_tts.assert_awaited_once()
        cog.stop_tts.assert_not_awaited()

    asyncio.run(scenario())


def make_tts_interaction():
    interaction = Mock()
    interaction.channel_id = 100
    # 実際の VoiceChannel と同じく、いまVCにいる人の一覧を持たせる
    interaction.user.voice.channel.members = []
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def test_start_tts_refuses_when_voice_channel_reads_another_channel():
    # 同じボイスチャンネルで別のテキストチャンネルを読み上げ中なら、接続せず断る
    async def scenario():
        cog = make_cog()
        cog.bot.tts_manager.state_for_voice = Mock(
            return_value=SimpleNamespace(text_channel_id=555)
        )
        cog.bot.voice_pool.acquire = AsyncMock()
        cog.repost_panel = AsyncMock()
        interaction = make_tts_interaction()

        await cog.start_tts(interaction)

        cog.bot.voice_pool.acquire.assert_not_awaited()
        args, kwargs = interaction.followup.send.call_args
        assert "<#555>" in args[0]
        assert kwargs["ephemeral"] is True
        cog.repost_panel.assert_not_awaited()

    asyncio.run(scenario())


def test_start_tts_runs_alongside_reading_in_another_voice_channel():
    # 別のボイスチャンネルなら、サーバー内で他が読み上げ中でも並行して始められる
    async def scenario():
        cog = make_cog()
        cog.bot.tts_manager.is_reading = Mock(return_value=True)
        cog.bot.tts_manager.state_for_voice = Mock(return_value=None)
        cog.bot.tts_manager.join_leave_notice = Mock(return_value=False)
        session = Mock()
        cog.bot.voice_pool.acquire = AsyncMock(return_value=session)
        cog.repost_panel = AsyncMock()
        interaction = make_tts_interaction()

        await cog.start_tts(interaction)

        cog.bot.tts_manager.start_reading.assert_called_once_with(100, session, False)
        cog.repost_panel.assert_awaited_once()

    asyncio.run(scenario())


def test_start_tts_reports_when_no_slot_is_free():
    from src.voice_pool import VoiceSlotUnavailable

    async def scenario():
        cog = make_cog()
        cog.bot.tts_manager.state_for_voice = Mock(return_value=None)
        cog.bot.voice_pool.acquire = AsyncMock(
            side_effect=VoiceSlotUnavailable("同時に使えるのは 1 つまで")
        )
        cog.repost_panel = AsyncMock()
        interaction = make_tts_interaction()

        await cog.start_tts(interaction)

        interaction.followup.send.assert_awaited_once_with(
            "同時に使えるのは 1 つまで", ephemeral=True
        )
        cog.bot.tts_manager.start_reading.assert_not_called()

    asyncio.run(scenario())


def test_stop_tts_stops_this_text_channel():
    async def scenario():
        cog = make_cog()
        cog.bot.tts_manager.stop_reading = AsyncMock()
        cog.repost_panel = AsyncMock()
        interaction = make_tts_interaction()

        await cog.stop_tts(interaction)

        cog.bot.tts_manager.stop_reading.assert_awaited_once_with(100)

    asyncio.run(scenario())


# ---- 大人数 VC でも全員を選べる(25 件を超える分は複数セレクトに分ける) ----

def make_player(name, user_id):
    return SimpleNamespace(name=name, user_id=user_id)


def make_players(count):
    return [make_player(f"メンバー{i}", i) for i in range(count)]


def assert_fits_discord_limits(view):
    rows = {}
    for item in view.children:
        rows.setdefault(item.row, []).append(item)
    assert max(rows) <= 4
    for items in rows.values():
        # セレクトは 1 行を丸ごと占有し、ボタンは 1 行 5 個まで
        if any(isinstance(item, discord.ui.Select) for item in items):
            assert len(items) == 1
        else:
            assert len(items) <= 5


def select_option_count(view):
    return sum(
        len(item.options) for item in view.children if isinstance(item, discord.ui.Select)
    )


def test_register_view_fits_limits_and_lists_everyone():
    for size in (1, MEMBER_BUTTON_LIMIT_SINGLE, MEMBER_BUTTON_LIMIT_SINGLE + 1, 60, 99):
        view = RegisterView(make_cog(), is_viewer=True, members=make_players(size))
        assert_fits_discord_limits(view)
        if size > MEMBER_BUTTON_LIMIT_SINGLE:
            # VC の上限(99 人)まで取りこぼさずセレクトに載ること
            assert select_option_count(view) == size
            assert view.hidden_member_count == 0


def test_delete_view_fits_limits_and_lists_everyone():
    for size in (1, MEMBER_BUTTON_LIMIT_DELETE, MEMBER_BUTTON_LIMIT_DELETE + 1, 99):
        view = DeleteView(make_cog(), is_viewer=True, members=make_players(size))
        assert_fits_discord_limits(view)
        if size > MEMBER_BUTTON_LIMIT_DELETE:
            assert select_option_count(view) == size
            assert view.hidden_member_count == 0


def test_random_view_fits_limits_and_reports_hidden_members():
    for size in (1, MEMBER_BUTTON_LIMIT_MULTI, MEMBER_BUTTON_LIMIT_MULTI + 1, 99):
        view = RandomView(make_cog(), size_values=[1, 2, 3], members=make_players(size))
        assert_fits_discord_limits(view)
    # 実行ボタン用に 1 行残すので 75 人まで。超えた分は黙らず件数を持つこと
    view = RandomView(make_cog(), size_values=[1], members=make_players(99))
    assert select_option_count(view) == 75
    assert view.hidden_member_count == 24


def test_random_view_switches_size_to_select_for_large_vc():
    view = RandomView(
        make_cog(), size_values=build_number_values("size", 30), members=make_players(3)
    )
    assert_fits_discord_limits(view)
    size_select = [item for item in view.children if isinstance(item, discord.ui.Select)]
    assert len(size_select) == 1 and len(size_select[0].options) == 25


def test_number_select_view_switches_to_select_for_large_vc():
    values = build_number_values("size", 30)
    view = NumberSelectView(make_cog(), "size", values, "人数")
    assert_fits_discord_limits(view)
    # 5 個を超える候補はセレクトに切り替え、30 人分すべて選べること
    assert select_option_count(view) == len(values)


def test_multi_select_keeps_choices_from_other_selects():
    view = DeleteView(make_cog(), is_viewer=True, members=make_players(30))
    selects = [item for item in view.children if isinstance(item, DeleteMultiSelect)]
    assert len(selects) == 2

    selects[0].apply_selection(["0", "1"])
    selects[1].apply_selection(["25"])
    # 別のセレクトで選んだ分を消さないこと
    assert set(view.selected_members) == {0, 1, 25}

    selects[0].apply_selection(["2"])
    # 選び直したセレクトの担当分だけ入れ替わること
    assert set(view.selected_members) == {2, 25}


def test_register_view_shows_selected_member_on_execute_button():
    view = RegisterView(make_cog(), is_viewer=True, members=make_players(30))
    view.select_member(7, "メンバー7")
    # セレクトが複数行でも、どの対象で実行されるか分かること
    assert view.execute_button.label == "実行 (メンバー7)"
    assert view.selected_user_id == 7


# ---- 時間切れを黙って失敗させない ----

def test_timeout_replaces_ephemeral_message():
    async def scenario():
        view = NumberSelectView(make_cog(), "team", [2, 3], "チーム数")
        interaction = Mock()
        interaction.edit_original_response = AsyncMock()
        view.bind(interaction)

        await view.on_timeout()

        kwargs = interaction.edit_original_response.call_args.kwargs
        assert kwargs["view"] is None
        assert "時間切れ" in kwargs["content"]

    asyncio.run(scenario())


def test_timeout_without_binding_is_silent():
    async def scenario():
        # bind していない View でも例外を出さないこと
        await NumberSelectView(make_cog(), "team", [2], "チーム数").on_timeout()

    asyncio.run(scenario())


# ---- パネルは 2 サーバー × 各 2 チャンネルまで ----

def test_check_panel_capacity_limits_channels_per_guild():
    cog = make_cog()
    cog.panel_store.set_panel(1, 101, 9001)
    cog.panel_store.set_panel(1, 102, 9002)

    error = cog.check_panel_capacity(make_channel(103, guild_id=1))
    # どのチャンネルで開いているかを示し、閉じ方を案内すること
    assert error is not None
    assert "<#101>" in error and "<#102>" in error and "/パネル終了" in error
    # 既に置いてあるチャンネルは貼り直せること
    assert cog.check_panel_capacity(make_channel(101, guild_id=1)) is None
    # 1 つ閉じれば空きができること
    cog.panel_store.delete_panel(102)
    assert cog.check_panel_capacity(make_channel(103, guild_id=1)) is None


def test_check_panel_capacity_limits_guild_count():
    cog = make_cog()
    cog.panel_store.set_panel(1, 101, 9001)
    cog.panel_store.set_panel(2, 201, 9002)

    error = cog.check_panel_capacity(make_channel(301, guild_id=3))
    assert error is not None and f"{MAX_PANEL_GUILDS} サーバー" in error
    # 既に使っているサーバーなら 2 チャンネル目まで置けること
    assert cog.check_panel_capacity(make_channel(102, guild_id=1)) is None


def test_show_panel_refuses_over_capacity_without_posting():
    async def scenario():
        cog = make_cog()
        for channel_id in range(101, 101 + MAX_PANELS_PER_GUILD):
            cog.panel_store.set_panel(1, channel_id, 9000 + channel_id)
        cog.repost_panel = AsyncMock()
        interaction = Mock()
        interaction.channel = make_channel(999, guild_id=1)
        interaction.response.send_message = AsyncMock()

        await cog.show_panel.callback(cog, interaction)

        # パネルを貼らず、本人にだけ理由を返すこと
        cog.repost_panel.assert_not_awaited()
        assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True

    asyncio.run(scenario())


# ---- 再起動後もパネルを増やさない ----

def test_repost_panel_records_position():
    async def scenario():
        cog = make_cog()
        cog.bot.tts_manager.is_target_channel = Mock(return_value=False)
        channel = make_channel(100, guild_id=1, message_id=900)

        await cog.repost_panel(channel)

        assert cog.panel_store.load_panels() == {100: (1, 900)}

    asyncio.run(scenario())


def test_repost_panel_deletes_stored_panel_after_restart():
    async def scenario():
        cog = make_cog()
        cog.bot.tts_manager.is_target_channel = Mock(return_value=False)
        channel = make_channel(100, guild_id=1, message_id=900)
        await cog.repost_panel(channel)

        # 再起動でメモリ上の追跡を失っても、保存した位置から古いパネルを消すこと
        restarted = PanelCog(cog.bot)
        old_panel = Mock()
        old_panel.delete = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=old_panel)
        channel.send = AsyncMock(return_value=Mock(id=901))

        await restarted.repost_panel(channel)

        channel.fetch_message.assert_awaited_once_with(900)
        old_panel.delete.assert_awaited_once()
        assert restarted.panel_store.load_panels() == {100: (1, 901)}

    asyncio.run(scenario())


def test_repost_panel_survives_missing_old_message():
    async def scenario():
        cog = make_cog()
        cog.bot.tts_manager.is_target_channel = Mock(return_value=False)
        channel = make_channel(100, guild_id=1, message_id=900)
        await cog.repost_panel(channel)

        # 手動削除などで古いパネルが取れなくても、新しいパネルは貼れること
        restarted = PanelCog(cog.bot)
        channel.fetch_message = AsyncMock(
            side_effect=discord.NotFound(Mock(status=404), "gone")
        )
        channel.send = AsyncMock(return_value=Mock(id=902))

        await restarted.repost_panel(channel)

        channel.send.assert_awaited_once()
        assert restarted.panel_store.load_panels() == {100: (1, 902)}

    asyncio.run(scenario())


def test_close_panel_frees_the_slot():
    async def scenario():
        cog = make_cog()
        cog.bot.tts_manager.is_target_channel = Mock(return_value=False)
        channel = make_channel(100, guild_id=1, message_id=900)
        await cog.repost_panel(channel)
        panel_message = cog.panel_messages[100]
        panel_message.delete = AsyncMock()

        interaction = Mock()
        interaction.channel = channel
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        await cog.close_panel.callback(cog, interaction)

        panel_message.delete.assert_awaited_once()
        assert cog.panel_store.load_panels() == {}

    asyncio.run(scenario())


# ---- 一覧表示 ----

def make_list_member(name, user_id, viewer=False, priority=False, rocked=False):
    return SimpleNamespace(
        name=name,
        user_id=user_id,
        viewer=viewer,
        priority=priority,
        rockedFlag=rocked,
        aspiration="",
    )


def test_run_show_all_requires_voice_channel():
    async def scenario():
        cog = make_cog()
        cog.split_team.set_member = Mock(return_value=False)
        cog.split_team.vc_err = "ボイスチャンネルに入ってください"
        cog.post_result = AsyncMock()
        interaction = Mock()
        interaction.response.send_message = AsyncMock()

        await cog.run_show_all(interaction)

        # VC 外から押しても、別 VC のメンバーを公開チャンネルへ出さないこと
        cog.post_result.assert_not_awaited()
        assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True

    asyncio.run(scenario())


def test_run_show_all_omits_empty_sections():
    async def scenario():
        cog = make_cog()
        cog.split_team.set_member = Mock(return_value=True)
        cog.split_team.channel_members = [
            make_list_member("アリス", 1),
            make_list_member("ボブ", 2, viewer=True, rocked=True),
        ]
        cog.post_result = AsyncMock()
        interaction = Mock()
        interaction.response.defer = AsyncMock()

        await cog.run_show_all(interaction)

        embed = cog.post_result.call_args.args[1]
        # 「なし」の行を並べず、該当者がいるセクションだけ出すこと
        assert [field.name for field in embed.fields] == ["チャンネルメンバー", "固定観戦者"]

    asyncio.run(scenario())


def test_run_show_all_reports_when_nothing_to_show():
    async def scenario():
        cog = make_cog()
        cog.split_team.set_member = Mock(return_value=True)
        cog.split_team.channel_members = []
        cog.post_result = AsyncMock()
        interaction = Mock()
        interaction.response.defer = AsyncMock()

        await cog.run_show_all(interaction)

        embed = cog.post_result.call_args.args[1]
        assert not embed.fields and embed.description
    asyncio.run(scenario())
