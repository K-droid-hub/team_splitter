import asyncio
import os
from unittest.mock import AsyncMock, Mock

os.environ.setdefault("GUILD_IDS", "0")  # panel の import 時に GUILD_IDS を参照するため

from src.modules.panel import (
    PanelCog,
    PanelView,
    VoiceButton,
    VoicePreviewButton,
    VoiceSelectView,
    build_number_values,
    build_pick_counts,
)
from src.modules.tts import SPEAKER_CHOICES


def test_build_number_values_team_starts_at_two():
    # チーム数は 1 チームを許さず 2 から始まる
    assert build_number_values("team", 4) == [2, 3, 4]


def test_build_number_values_size_starts_at_one():
    assert build_number_values("size", 4) == [1, 2, 3, 4]


def test_build_number_values_capped_at_max_buttons():
    # メンバーが多くてもボタンは 5 個まで
    assert build_number_values("size", 20) == [1, 2, 3, 4, 5]
    assert build_number_values("team", 20) == [2, 3, 4, 5, 6]


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
    return PanelCog(bot)


def test_voice_select_view_pairs_speaker_with_preview():
    async def scenario():
        view = VoiceSelectView(make_cog())
        speakers = view.children[0::2]
        previews = view.children[1::2]
        # 各話者が「話者ボタン + 試聴ボタン」の 2 つを持つこと
        assert len(view.children) == len(SPEAKER_CHOICES) * 2
        assert all(isinstance(b, VoiceButton) for b in speakers)
        assert all(isinstance(b, VoicePreviewButton) for b in previews)
        for index, choice in enumerate(SPEAKER_CHOICES):
            # 話者と試聴が同じ行に隣接し、同じ話者を指すこと
            assert speakers[index].label == choice.name
            assert speakers[index].speaker_value == choice.value
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

def make_channel(channel_id, guild_id):
    channel = Mock()
    channel.id = channel_id
    channel.guild.id = guild_id
    channel.send = AsyncMock(return_value=Mock())
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


def test_start_tts_refuses_when_reading_elsewhere():
    # 別チャンネルで読み上げ中なら、VC 接続せず拒否メッセージを返す
    async def scenario():
        cog = make_cog()
        cog.bot.tts_manager.is_reading = Mock(return_value=True)
        interaction = Mock()
        interaction.response.send_message = AsyncMock()
        interaction.user.voice.channel.connect = AsyncMock()

        await cog.start_tts(interaction)

        interaction.response.send_message.assert_awaited_once()
        assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True
        interaction.user.voice.channel.connect.assert_not_awaited()

    asyncio.run(scenario())
