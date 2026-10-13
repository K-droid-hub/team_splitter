from src.panel_store import PanelStore


def make_store():
    # ファイルを作らないインメモリ DB のストアを返す
    return PanelStore(db_path=":memory:")


def test_set_and_load_panels():
    store = make_store()
    store.set_panel(1, 101, 9001)
    store.set_panel(2, 201, 9002)
    assert store.load_panels() == {101: (1, 9001), 201: (2, 9002)}


def test_same_channel_overwrites_message_id():
    # 貼り直すと同じチャンネルの message_id が差し替わる(行は増えない)
    store = make_store()
    store.set_panel(1, 101, 9001)
    store.set_panel(1, 101, 9002)
    assert store.load_panels() == {101: (1, 9002)}


def test_delete_panel_frees_the_slot():
    store = make_store()
    store.set_panel(1, 101, 9001)
    store.delete_panel(101)
    assert store.load_panels() == {}


def test_delete_missing_panel_is_noop():
    store = make_store()
    store.set_panel(1, 101, 9001)
    store.delete_panel(999)
    assert store.load_panels() == {101: (1, 9001)}


def test_persists_across_reconnect(tmp_path):
    # bot を再起動しても設置位置が残り、古いパネルを消せることを別接続で確認する
    db_path = str(tmp_path / "panels.db")
    first = PanelStore(db_path=db_path)
    first.set_panel(1, 101, 9001)
    second = PanelStore(db_path=db_path)
    assert second.load_panels() == {101: (1, 9001)}
