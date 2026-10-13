from src.user_aspiration_store import UserAspirationStore


def make_store():
    # ファイルを作らないインメモリ DB のストアを返す
    return UserAspirationStore(db_path=":memory:")


def test_set_and_load_by_user():
    store = make_store()
    store.set_aspiration(100, 1, "がんばる", "アリス")
    store.set_aspiration(100, 2, "勝つぞ", "ボブ")
    assert store.load_aspirations_by_user() == {
        (100, 1): "がんばる",
        (100, 2): "勝つぞ",
    }


def test_same_user_overwrites():
    # 同じ guild_id・user_id で保存し直すと上書きされる
    store = make_store()
    store.set_aspiration(100, 1, "旧", "アリス")
    store.set_aspiration(100, 1, "新", "アリス改名")
    assert store.load_aspirations_by_user() == {(100, 1): "新"}


def test_aspiration_is_scoped_per_guild():
    # 同じユーザーでもサーバー(guild)が違えば別々に保持される
    store = make_store()
    store.set_aspiration(100, 1, "サーバーAの意気込み", "アリス")
    store.set_aspiration(200, 1, "サーバーBの意気込み", "アリス")
    assert store.load_aspirations_by_user() == {
        (100, 1): "サーバーAの意気込み",
        (200, 1): "サーバーBの意気込み",
    }


def test_empty_aspiration_is_excluded():
    # 空文字の意気込みは表示辞書から除外される(削除相当)
    store = make_store()
    store.set_aspiration(100, 1, "", "アリス")
    assert store.load_aspirations_by_user() == {}


def test_persists_across_reconnect(tmp_path):
    # bot を落として再起動しても保持されることを、別接続の再読込で確認する
    db_path = str(tmp_path / "aspirations.db")
    first = UserAspirationStore(db_path=db_path)
    first.set_aspiration(100, 1, "続くよ", "アリス")
    second = UserAspirationStore(db_path=db_path)
    assert second.load_aspirations_by_user() == {(100, 1): "続くよ"}
