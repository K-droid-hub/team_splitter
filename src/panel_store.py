import os
import sqlite3

# DB ファイルの既定パス。WORKDIR /app 前提で volume マウント先に載る相対パス
DEFAULT_DB_PATH = "data/panels.db"


class PanelStore:
    """設置済み操作パネルの位置を SQLite に永続化するストア"""

    def __init__(self, db_path=None):
        self.db_path = db_path or os.getenv("PANEL_DB_PATH", DEFAULT_DB_PATH)
        self.connection = self._connect_db()
        self._create_table()

    def _connect_db(self):
        # :memory: 以外は親ディレクトリを用意してから接続する
        if self.db_path != ":memory:":
            directory = os.path.dirname(self.db_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
        # 別スレッドからの利用に備え、他のストアと同じ作法で接続する
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _create_table(self):
        # パネルは 1 チャンネルに 1 つなので channel_id を主キーにする。
        # guild_id も持つのは、サーバー単位の設置数上限を数えるため
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS panels ("
            "channel_id INTEGER PRIMARY KEY, "
            "guild_id INTEGER NOT NULL, "
            "message_id INTEGER)"
        )
        self.connection.commit()

    # パネルの位置を 1 件保存する。同じチャンネルなら貼り直し後の message_id で上書きする
    def set_panel(self, guild_id, channel_id, message_id):
        self.connection.execute(
            "INSERT INTO panels (channel_id, guild_id, message_id) VALUES (?, ?, ?) "
            "ON CONFLICT(channel_id) DO UPDATE SET "
            "guild_id = excluded.guild_id, "
            "message_id = excluded.message_id",
            (channel_id, guild_id, message_id),
        )
        self.connection.commit()

    def delete_panel(self, channel_id):
        self.connection.execute("DELETE FROM panels WHERE channel_id = ?", (channel_id,))
        self.connection.commit()

    # 保存済みのパネルを channel_id -> (guild_id, message_id) の dict で返す
    def load_panels(self):
        rows = self.connection.execute(
            "SELECT channel_id, guild_id, message_id FROM panels"
        ).fetchall()
        return {
            channel_id: (guild_id, message_id) for channel_id, guild_id, message_id in rows
        }
