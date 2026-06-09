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
        self.channel_members = [] # Player クラスのインスタンスのリスト
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

    def _build_table(self, ctx, factor, factor_label):
        #チャンネルメンバーリセット & チャンネルに誰もいなかった時の処理
        if self.set_member(ctx) is False:
            return self.vc_err
        if factor > self.member_size:
            return f"指定した{factor_label}が多すぎます。チャンネルメンバー数に合った{factor_label}を指定してください。"
        elif factor <= 0:
            return f"{factor_label}は1以上を指定してください。"
        # 観戦者を抜いたプレイヤーリスト Table を作成
        return [p for p in self.channel_members if not p.viewer]

    def _reset_temporaries(self):
        # チーム分けが終わった後、固定されていない優先者と観戦者をリセット
        for p in self.channel_members:
            if not p.locked:
                p.priority = False
            if p.viewer and not p.locked:
                p.viewer = False

    def split_by_team_count(self, ctx, team_count: int) -> str:
        table = self._build_table(ctx, team_count, "チーム数")
        # エラーメッセージが返ってきた場合はそれを返す
        if isinstance(table, str):
            return table
        random.shuffle(table)
        # チーム数分の空のリストを用意し、1人ずつ順番に配属していく
        teams = [[] for _ in range(team_count)]
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
        table = self._build_table(ctx, team_size, "1チームあたりの人数")
        # エラーメッセージが返ってきた場合はそれを返す
        if isinstance(table, str):
            return table
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
        table = self._build_table(ctx, size, "人数")
        # エラーメッセージが返ってきた場合はそれを返す
        if isinstance(table, str):
            return table
        random.shuffle(table)
        results = ["=====抽選結果====="]
        # 優先者を引っこ抜き、先に追加する
        priorities = [p for p in table if p.priority]
        for p in priorities:
            table.remove(p)
        while priorities and size > 0:
            random.shuffle(priorities)
            results.append(priorities.pop().name)
            size -= 1
        if size > 0:
            for _ in range(size):
                results.append(table.pop().name)
        self._reset_temporaries()
        return '\n'.join(results)

    def add_special(self, ctx, addName, isViewer, isPriority, isRocked):
        # フラッグでどちらを追加するか決定
        if isViewer:
            main_factor_name = "観戦者"
        else:
            main_factor_name = "優先者"
        #チャンネルメンバー再セット & チャンネルに誰もいなかった時の処理
        if self.set_member(ctx) is False: 
            return self.vc_err
        for member in self.channel_members:
            if member.name == addName:
                if isViewer:
                    if member.priority: # 優先者だった場合解除する
                        member.priority = False
                        member.locked = False
                    member.viewer = True
                elif isPriority:
                    if member.viewer: # 観戦者だった場合解除する
                        member.viewer = False
                        member.locked = False
                    member.priority = True
                if isRocked:
                    member.locked = True
                    result = f"{main_factor_name}にして固定しました"
                else:
                    result = f"次だけ{main_factor_name}にしました"
                result += f" \n{addName}"
                break
        else:
            result = "メンバーが見つかりません"
        return result

    def delete_special(self, deleteName, isViewer, isPriority):
        # フラッグでどちらを削除するか決定
        if isViewer:
            main_factor_name = "観戦者"
        else:
            main_factor_name = "優先者"
        for member in self.channel_members:
            if member.name == deleteName:
                if isViewer and member.viewer:
                    member.viewer = False
                    member.locked = False
                    result = f"{main_factor_name}を解除しました。"
                elif isPriority and member.priority:
                    member.priority = False
                    member.locked = False
                    result = f"{main_factor_name}を解除しました。"
                else:
                    result = f"そのメンバーは{main_factor_name}ではありません"
                result += f" \n{deleteName}"
                break
        else:
            result = "メンバーが見つかりません"
        return result

    def reset_all(self):
        self.channel_members.clear()
        self.member_size = 0