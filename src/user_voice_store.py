import os
import sqlite3

# DB ファイルの既定パス。WORKDIR /app 前提で volume マウント先に載る相対パス
DEFAULT_DB_PATH = "data/voices.db"


class UserVoiceStore:
    """ユーザーごとの読み上げ話者を SQLite に永続化するストア"""

    def __init__(self, db_path=None):
        self.db_path = db_path or os.getenv("VOICE_DB_PATH", DEFAULT_DB_PATH)
        self.connection = self._connect_db()
        self._create_table()

    def _connect_db(self):
        # :memory: 以外は親ディレクトリを用意してから接続する
        if self.db_path != ":memory:":
            directory = os.path.dirname(self.db_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
        # 音声再生は別スレッドを使うため、同一プロセス内の別スレッド利用を許可する
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _create_table(self):
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS user_voices ("
            "user_id INTEGER PRIMARY KEY, "
            "speaker_id INTEGER NOT NULL, "
            "display_name TEXT)"
        )
        self.connection.commit()

    # 話者設定を 1 件保存する。既存なら上書きする
    def set_speaker(self, user_id, speaker_id, display_name=None):
        self.connection.execute(
            "INSERT INTO user_voices (user_id, speaker_id, display_name) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "speaker_id = excluded.speaker_id, "
            "display_name = excluded.display_name",
            (user_id, speaker_id, display_name),
        )
        self.connection.commit()

    # 保存済みの全設定を user_id -> speaker_id の dict で返す
    def load_all_speakers(self):
        rows = self.connection.execute(
            "SELECT user_id, speaker_id FROM user_voices"
        ).fetchall()
        return {user_id: speaker_id for user_id, speaker_id in rows}
