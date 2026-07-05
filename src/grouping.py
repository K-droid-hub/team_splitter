import random
import discord
import math
import dataclasses

@dataclasses.dataclass
class Player:
    name: str
    priority: bool = False
    viewer: bool = False
    locked: bool = False  # 固定フラグ

class SplitTeam:
    def __init__(self):
        self.channel_members: list[Player] = []
        self.member_size = 0
        self.vc_err = '実行できません。ボイスチャンネルに入ってコマンドを実行してください。'

    # VCメンバーのセット
    def set_member(self, interaction_or_ctx):
        # VCに誰かいるか確認
        if isinstance(interaction_or_ctx, discord.Interaction):
            state = interaction_or_ctx.user.voice
        else:
            state = interaction_or_ctx.author.voice
        if state is None:
            return False
        # 現在のネームリストを取得 (bot は除く)
        current_vc_members = [
            i.display_name for i in state.channel.members
            if not i.bot
        ]
        # 前回実行時からいなくなったメンバーを削除
        self.channel_members = [p for p in self.channel_members if p.name in current_vc_members]
        # 既存メンバーのネームリスト
        existing_names = [p.name for p in self.channel_members]
        # 前回実行時との差分をとり、既存のメンバーの情報を引き継ぎつつ、メンバーリストを更新
        for name in current_vc_members:
            if name not in existing_names:
                self.channel_members.append(Player(name=name))
        self.member_size = len(self.channel_members)
        return True

    def _build_table(self, ctx, factor: int, factor_label: str) -> list[Player]:
        """VCメンバーを読み込み、観戦者を除いたプレイヤーリストを返す。エラー時は ValueError を raise する。"""
        if self.set_member(ctx) is False:
            raise ValueError(self.vc_err)
        if factor > self.member_size:
            raise ValueError(f"指定した{factor_label}が多すぎます。チャンネルメンバー数に合った{factor_label}を指定してください。")
        if factor <= 0:
            raise ValueError(f"{factor_label}は1以上を指定してください。")
        return [p for p in self.channel_members if not p.viewer]

    def _reset_temporaries(self):
        # チーム分けが終わった後、固定されていない優先者と観戦者をリセット
        for p in self.channel_members:
            if not p.locked:
                p.priority = False
            if p.viewer and not p.locked:
                p.viewer = False

    def _find_member(self, name: str) -> Player | None:
        return next((p for p in self.channel_members if p.name == name), None)

    def split_by_team_count(self, ctx, team_count: int) -> str:
        try:
            table = self._build_table(ctx, team_count, "チーム数")
        except ValueError as e:
            return str(e)
        random.shuffle(table)
        # チーム数分の空のリストを用意し、1人ずつ順番に配属していく
        teams: list[list[str]] = [[] for _ in range(team_count)]
        for i, p in enumerate(table):
            teams[i % team_count].append(p.name)
        # 出力をまとめる
        results = []
        for i, members in enumerate(teams):
            results.append(f"=====チーム{i + 1}=====")
            results.extend(members)
        self._reset_temporaries()
        return '\n'.join(results)

    def split_by_team_size(self, ctx, team_size: int) -> str:
        try:
            table = self._build_table(ctx, team_size, "1チームあたりの人数")
        except ValueError as e:
            return str(e)
        random.shuffle(table)
        # チーム数を計算して、チーム数分の空のリストを用意
        team_count = math.ceil(len(table) / team_size)
        results = []
        for i in range(team_count):
            results.append(f"=====チーム{i + 1}=====")
            results.extend([p.name for p in table[i * team_size : (i + 1) * team_size]])
        self._reset_temporaries()
        return '\n'.join(results)

    def split_by_lottery(self, ctx, size: int) -> str:
        try:
            table = self._build_table(ctx, size, "人数")
        except ValueError as e:
            return str(e)
        random.shuffle(table)
        # 優先者を先に確定し、残り枠をランダムから補充
        priorities = [p for p in table if p.priority]
        others = [p for p in table if not p.priority]
        picked = random.sample(priorities, min(size, len(priorities)))
        picked += others[: size - len(picked)]
        results = ["=====抽選結果====="] + [p.name for p in picked]
        self._reset_temporaries()
        return '\n'.join(results)

    def add_viewer(self, ctx, name: str, lock: bool = False) -> str:
        if self.set_member(ctx) is False:
            return self.vc_err
        member = self._find_member(name)
        if member is None:
            return "メンバーが見つかりません"
        if member.priority:  # 優先者だった場合解除する
            member.priority = False
            member.locked = False
        member.viewer = True
        member.locked = lock
        label = "観戦者にして固定しました" if lock else "次だけ観戦者にしました"
        return f"{label}\n{name}"

    def add_priority(self, ctx, name: str, lock: bool = False) -> str:
        if self.set_member(ctx) is False:
            return self.vc_err
        member = self._find_member(name)
        if member is None:
            return "メンバーが見つかりません"
        if member.viewer:  # 観戦者だった場合解除する
            member.viewer = False
            member.locked = False
        member.priority = True
        member.locked = lock
        label = "優先者にして固定しました" if lock else "次だけ優先者にしました"
        return f"{label}\n{name}"

    def delete_viewer(self, name: str) -> str:
        member = self._find_member(name)
        if member is None:
            return "メンバーが見つかりません"
        if not member.viewer:
            return f"そのメンバーは観戦者ではありません\n{name}"
        member.viewer = False
        member.locked = False
        return f"観戦者を解除しました。\n{name}"

    def delete_priority(self, name: str) -> str:
        member = self._find_member(name)
        if member is None:
            return "メンバーが見つかりません"
        if not member.priority:
            return f"そのメンバーは優先者ではありません\n{name}"
        member.priority = False
        member.locked = False
        return f"優先者を解除しました。\n{name}"

    def reset_all(self):
        self.channel_members.clear()
        self.member_size = 0
