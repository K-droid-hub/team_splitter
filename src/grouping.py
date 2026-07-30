import random
import discord
import math
import dataclasses

from src.user_aspiration_store import UserAspirationStore

@dataclasses.dataclass
class player:
    name: str
    priority: bool = False
    viewer: bool = False
    rockedFlag: bool = False
    aspiration: str = ""


# 意気込みがある人だけ 2 行目に斜体で添える。無い人は行を詰めて名前のみ返す
def format_member_entry(name, aspiration):
    if aspiration:
        return f"{name}\n*{aspiration}*"
    return name


# player のリストを、意気込み付きの改行連結テキストにする。空なら "なし"
def format_member_list(members):
    if not members:
        return "なし"
    return "\n".join(format_member_entry(m.name, m.aspiration) for m in members)

class SplitTeam:
    def __init__(self, aspiration_store=None):
        self.channel_members = [] # player クラスのインスタンスのリスト
        self.memberSize = 0
        self.vc_err = '実行できません。ボイスチャンネルに入ってコマンドを実行してください。'
        # 意気込みは DB に永続化し、名前ベースで引き当てられるよう辞書で保持する
        self.aspiration_store = aspiration_store or UserAspirationStore()
        self.aspirations_by_name = self.aspiration_store.load_aspirations_by_name()
        
    # VCメンバーのセット
    def set_member(self, interaction_or_ctx):
        # VCに誰かいるか確認。意気込みはサーバーごとに引き当てるので guild_id も控える
        if isinstance(interaction_or_ctx, discord.Interaction):
            state = interaction_or_ctx.user.voice
        else:
            state = interaction_or_ctx.author.voice
        guild_id = interaction_or_ctx.guild.id
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
                self.channel_members.append(player(name=name))
        # DB 更新を反映するため、毎回全メンバーの意気込みを引き当て直す(サーバー単位)
        for p in self.channel_members:
            p.aspiration = self.aspirations_by_name.get((guild_id, p.name), "")
        self.memberSize = len(self.channel_members)
        return True

    # 意気込みを DB へ保存し、名前ベースの辞書も更新する。空文字は削除扱い
    def set_aspiration(self, guild_id, user_id, display_name, aspiration):
        self.aspiration_store.set_aspiration(guild_id, user_id, aspiration, display_name)
        if aspiration:
            self.aspirations_by_name[(guild_id, display_name)] = aspiration
        else:
            self.aspirations_by_name.pop((guild_id, display_name), None)

    def makeTable(self, ctx, main_factor, main_factor_name):  
        #チャンネルメンバーリセット & チャンネルに誰もいなかった時の処理
        if self.set_member(ctx) is False: 
            return self.vc_err
        if main_factor > self.memberSize: 
            return f"指定した{main_factor_name}が多すぎます。チャンネルメンバー数に合った{main_factor_name}を指定してください。"
        elif main_factor <= 0: 
            return f"{main_factor_name}は1以上を指定してください。"
        # 観戦者を抜いたプレイヤーリスト Table を作成
        return [player for player in self.channel_members if not player.viewer] 
    
    def split(self, ctx, teamCount, teamSize):
        # 指定された数値によって、どちらに重点を置いてチーム分けするかを決定
        if teamCount > 0 and teamSize == 0:
            main_factor = teamCount
            main_factor_name = "チーム数"
        elif teamSize > 0 and teamCount == 0:
            main_factor = teamSize
            main_factor_name = "1チームあたりの人数"
        else: # 両方指定されている場合 (/人数分抽選用)
            main_factor = teamSize # 優先して人数で分ける (チーム数は 1 で固定)
            main_factor_name = "人数"
        table = self.makeTable(ctx, main_factor, main_factor_name)
        # エラーメッセージが返ってきた場合はそれを返す
        if isinstance(table, str): 
            return table
        random.shuffle(table)
        # 　main_factor に適した方法で、チーム分けを実行
        if main_factor_name == "チーム数":
            # チーム数分の空のリストを用意
            teams = [[] for _ in range(teamCount)]
            # 1人ずつ順番にチームに配属していく
            for i, p in enumerate(table):
                teams[i % teamCount].append(p)
            results = []
            # 出力をまとめる
            for i, members in enumerate(teams):
                results.append(f"=====チーム{i + 1}=====")
                results.extend(format_member_entry(p.name, p.aspiration) for p in members)
        elif main_factor_name == "1チームあたりの人数":
            # チーム数を計算して、チーム数分の空のリストを用意
            teamCount = math.ceil(len(table) / teamSize)
            results = []
            for i in range(teamCount):
                results.append(f"=====チーム{i + 1}=====")
                results.extend(
                    format_member_entry(p.name, p.aspiration)
                    for p in table[i * teamSize : (i + 1) * teamSize]
                )
        else:
            results = ["=====抽選結果====="]
            # 優先者を引っこ抜き、先に追加する
            priorities = [p for p in table if p.priority]
            for p in priorities:
                table.remove(p)
            while priorities and teamSize > 0:
                random.shuffle(priorities)
                p = priorities.pop()
                results.append(format_member_entry(p.name, p.aspiration))
                teamSize -= 1
            if teamSize > 0:
                for _ in range(teamSize):
                    p = table.pop()
                    results.append(format_member_entry(p.name, p.aspiration))
        # チーム分けが終わった後、固定されていない優先者と観戦者をリセット     
        for player in self.channel_members:
            if not player.rockedFlag:
                player.priority = False 
            if player.viewer and not player.rockedFlag: 
                player.viewer = False
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
                        member.rockedFlag = False
                    member.viewer = True 
                elif isPriority:
                    if member.viewer: # 観戦者だった場合解除する
                        member.viewer = False
                        member.rockedFlag = False
                    member.priority = True
                if isRocked:
                    member.rockedFlag = True
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
                    member.rockedFlag = False
                    result = f"{main_factor_name}を解除しました。"
                elif isPriority and member.priority:
                    member.priority = False
                    member.rockedFlag = False 
                    result = (f"{main_factor_name}を解除しました。")
                else:
                    result = f"そのメンバーは{main_factor_name}ではありません"
                result += f" \n{deleteName}"
                break
        else:
            result = "メンバーが見つかりません"
        return result

    def reset_all(self):
        self.channel_members.clear()
        self.memberSize = 0