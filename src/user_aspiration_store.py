import os
import sqlite3

# DB ファイルの既定パス。WORKDIR /app 前提で volume マウント先に載る相対パス
DEFAULT_DB_PATH = "data/aspirations.db"


class UserAspirationStore:
    """ユーザーごとの意気込みを SQLite に永続化するストア"""

    def __init__(self, db_path=None):
        self.db_path = db_path or os.getenv("ASPIRATION_DB_PATH", DEFAULT_DB_PATH)
        self.connection = self._connect_db()
        self._create_table()

    def _connect_db(self):
        # :memory: 以外は親ディレクトリを用意してから接続する
        if self.db_path != ":memory:":
            directory = os.path.dirname(self.db_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
        # 別スレッドからの利用に備え、voice store と同じ作法で接続する
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _create_table(self):
        # 意気込みはサーバー(guild)ごとに独立させるため、(guild_id, user_id) を主キーにする
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS user_aspirations ("
            "guild_id INTEGER NOT NULL, "
            "user_id INTEGER NOT NULL, "
            "aspiration TEXT, "
            "display_name TEXT, "
            "PRIMARY KEY (guild_id, user_id))"
        )
        self.connection.commit()

    # 意気込みを 1 件保存する。同じ guild_id・user_id なら上書きする
    def set_aspiration(self, guild_id, user_id, aspiration, display_name=None):
        self.connection.execute(
            "INSERT INTO user_aspirations (guild_id, user_id, aspiration, display_name) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(guild_id, user_id) DO UPDATE SET "
            "aspiration = excluded.aspiration, "
            "display_name = excluded.display_name",
            (guild_id, user_id, aspiration, display_name),
        )
        self.connection.commit()

    # 保存済みの意気込みを (guild_id, display_name) -> aspiration の dict で返す。
    # 表示は名前ベースなので、空の意気込み・名前欠落の行は除外する
    def load_aspirations_by_name(self):
        rows = self.connection.execute(
            "SELECT guild_id, display_name, aspiration FROM user_aspirations"
        ).fetchall()
        return {
            (guild_id, display_name): aspiration
            for guild_id, display_name, aspiration in rows
            if display_name and aspiration
        }
