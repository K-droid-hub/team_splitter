import asyncio

from src.voice_session import VoiceSession


class VoiceSlotUnavailable(Exception):
    """空いている bot がなく、ボイス接続を増やせないときに送出する"""


class VoicePool:
    """複数の bot を束ね、ボイスチャンネルごとに担当 bot を割り当てる。

    Discord は bot アカウント 1 つにつき 1 サーバー 1 接続までなので、
    サーバーで同時に使えるボイスチャンネルの数は、そこにいる bot の数で決まる
    """

    def __init__(self, clients):
        # 並び順がそのまま優先順位。先頭の bot から使い、2 体目は
        # 2 つ目のボイスチャンネルが要るときにだけ姿を見せる
        self.clients = [c for c in clients if c is not None]
        self.sessions = []
        # 「空きを調べる」と「接続する」の間に別の要求が割り込むと同じ bot を
        # 掴み合うので、サーバー単位で直列化する
        self._locks = {}

    def _lock(self, guild_id):
        lock = self._locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[guild_id] = lock
        return lock

    # 接続状態は discord.py 側から読む。二重管理による食い違いを避ける
    def _voice_client_of(self, client, guild_id):
        guild = client.get_guild(guild_id)
        return None if guild is None else guild.voice_client

    # このサーバーに招待されている bot の数。同時に使えるボイスチャンネルの上限になる
    def capacity(self, guild_id):
        return sum(1 for c in self.clients if c.get_guild(guild_id) is not None)

    def session_for_channel(self, channel_id):
        for session in self.sessions:
            if session.is_alive() and session.channel_id == channel_id:
                return session
        return None

    def sessions_in_guild(self, guild_id):
        return [
            s for s in self.sessions if s.is_alive() and s.guild_id == guild_id
        ]

    # 指定チャンネルの接続を返す。既に bot がいればそこへ相乗りし、
    # いなければ空いている bot を優先順に割り当てる
    async def acquire(self, channel, user):
        guild_id = channel.guild.id
        async with self._lock(guild_id):
            # 外から切断された接続が残っていると判定を誤るので、先に片付ける
            self.sessions = [s for s in self.sessions if s.is_alive()]
            # 1. 同じチャンネルにいる bot へ相乗りする。2 体目を呼ぶと並んでしまうため
            session = self.session_for_channel(channel.id)
            if session is not None:
                session.retain(user)
                return session
            # 2. 空いている bot を優先順に割り当てる
            for client in self.clients:
                # 起動直後はキャッシュが空で、未招待と区別できないので飛ばす
                if not client.is_ready():
                    continue
                if self._voice_client_of(client, guild_id) is not None:
                    continue
                # 各 bot は自分のキャッシュ上のチャンネルにしか接続できない。
                # 取れないのはこのサーバーに招待されていないということ
                target = client.get_channel(channel.id)
                if target is None:
                    continue
                voice_client = await target.connect()
                session = VoiceSession(voice_client, on_close=self._forget)
                session.retain(user)
                self.sessions.append(session)
                return session
            # 3. 全員埋まっている
            raise VoiceSlotUnavailable(self._full_message(guild_id))

    def _forget(self, session):
        if session in self.sessions:
            self.sessions.remove(session)

    # どこで使っているかを添えると、そちらへ移ればいいと分かる
    def _full_message(self, guild_id):
        limit = self.capacity(guild_id)
        names = "、".join(f"🔊{s.channel.name}" for s in self.sessions_in_guild(guild_id))
        message = f"このサーバーで同時に使えるボイスチャンネルは {limit} つまでです。"
        if names:
            message += f"(今は {names} で使用中)"
        return message
