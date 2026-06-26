"""
fetch.py
---------
unite-db.com/pokemon.json からポケモンデータを取得し、
日本語名マッピングを通じて CSV と紐付ける。

提供する情報:
    - tier / soloQtier（最新 Tier 情報）
    - role / range / difficulty（公式タグ）
    - damage_type（Physical / Special）
    - lane（推奨レーン傾向）
    - 新ポケモン検知（CSV未登録のポケモン名を通知）
"""

from __future__ import annotations
import json
import urllib.request
from pathlib import Path

API_URL = "https://unite-db.com/pokemon.json"
MAPPING_FILE = Path("data/name_mapping.json")


def _load_mapping() -> dict[str, list[str]]:
    with MAPPING_FILE.open(encoding="utf-8") as f:
        return json.load(f)


def fetch_all() -> list[dict]:
    """API から全ポケモンデータを取得して返す。"""
    with urllib.request.urlopen(API_URL, timeout=10) as r:
        return json.load(r)


def get_pokemon_info(en_name: str, data: list[dict] | None = None) -> dict | None:
    """英語名でポケモン情報を取得する。"""
    if data is None:
        data = fetch_all()
    for p in data:
        if p["name"] == en_name:
            return p
    return None


def build_ja_index(data: list[dict] | None = None) -> dict[str, dict]:
    """
    日本語名 → API データ のインデックスを作成する。
    1対多の場合（Pikachu → ピカチュウ(ボルテ), ピカチュウ(10万)）は同じデータを共有。
    """
    if data is None:
        data = fetch_all()
    mapping = _load_mapping()
    api_by_name = {p["name"]: p for p in data}

    index: dict[str, dict] = {}
    for en_name, ja_names in mapping.items():
        api_data = api_by_name.get(en_name)
        if api_data is None:
            continue
        for ja in ja_names:
            index[ja] = api_data
    return index


def get_tier(ja_name: str, index: dict[str, dict] | None = None) -> dict | None:
    """日本語名で tier 情報を返す。"""
    if index is None:
        index = build_ja_index()
    p = index.get(ja_name)
    if p is None:
        return None
    return {
        "tier":            p.get("tier"),
        "tier_change":     p.get("tier_change"),
        "soloQtier":       p.get("soloQtier"),
        "solo_tier_change": p.get("solo_tier_change"),
        "last_updated":    p.get("last_updated"),
    }


def get_lanes(ja_name: str, index: dict[str, dict] | None = None) -> list[str]:
    """日本語名で推奨レーン一覧を返す（重複除去）。"""
    if index is None:
        index = build_ja_index()
    p = index.get(ja_name)
    if p is None:
        return []
    return list(dict.fromkeys(b["lane"] for b in p.get("builds", []) if b.get("lane")))


def detect_new_pokemon(csv_ja_names: list[str]) -> list[str]:
    """
    API にいて CSV に未登録のポケモン（英語名）を返す。
    新ポケモン追加時の通知に使う。
    """
    mapping = _load_mapping()
    csv_set = set(csv_ja_names)
    new_pokemon = []
    for en_name, ja_names in mapping.items():
        if not ja_names:
            new_pokemon.append(en_name)
        elif not any(ja in csv_set for ja in ja_names):
            new_pokemon.append(en_name)
    return new_pokemon


if __name__ == "__main__":
    import pandas as pd

    df = pd.read_csv("roles-test.csv", encoding="shift_jis", index_col=0)
    df.index.name = "name"
    df = df.reset_index()
    df = df[df["name"].notna() & (df["name"].str.strip() != "")]
    csv_names = df["name"].tolist()

    print("APIデータ取得中...")
    data = fetch_all()
    index = build_ja_index(data)

    print(f"\n取得済みポケモン数: {len(data)}")
    print(f"日本語マッピング済み: {len(index)} エントリ")

    print("\n--- Tier サンプル ---")
    for name in ["ゲンガー", "カメックス", "ブラッキー", "ピカチュウ (ボルテ)"]:
        info = get_tier(name, index)
        print(f"  {name}: チーム={info['tier']} ソロ={info['soloQtier']} ({info['last_updated']})")

    print("\n--- レーン サンプル ---")
    for name in ["ゲンガー", "ブラッキー", "ミライドン(チャービ)"]:
        lanes = get_lanes(name, index)
        print(f"  {name}: {lanes}")

    print("\n--- CSV未登録ポケモン ---")
    new_ones = detect_new_pokemon(csv_names)
    print(f"  {new_ones if new_ones else 'なし'}")
