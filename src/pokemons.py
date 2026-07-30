import random
import ssl

import aiohttp
import certifi

# ポケモンユナイト参戦ポケモンのライブデータ (unite-db.com が公開している JSON)
POKEMON_JSON_URL = "https://unite-db.com/pokemon.json"

# 取得タイムアウト (秒)。約 3MB あるので短すぎると初回取得に失敗する
FETCH_TIMEOUT_SECONDS = 20

# ライブ取得に失敗したときのフォールバック。unite-db.com の display_name スナップショット
FALLBACK_POKEMONS = [
    'Absol', 'Aegislash', 'Alcremie', 'Armarouge',
    'Articuno', 'Azumarill', 'Blastoise', 'Blaziken',
    'Blissey', 'Buzzwole', 'Ceruledge', 'Chandelure',
    'Charizard', 'Mega Charizard X', 'Mega Charizard Y', 'Cinderace',
    'Clefable', 'Comfey', 'Cramorant', 'Crustle',
    'Darkrai', 'Decidueye', 'Delphox', 'Dhelmise',
    'Dodrio', 'Dragapult', 'Dragonite', 'Duraludon',
    'Eldegoss', 'Empoleon', 'Espeon', 'Falinks',
    'Feraligatr', 'Garchomp', 'Gardevoir', 'Gengar',
    'Glaceon', 'Goodra', 'Greedent', 'Greninja',
    'Gyarados', 'Mega Gyarados', 'Ho-Oh', 'Hoopa',
    'Inteleon', 'Lapras', 'Latias', 'Latios',
    'Leafeon', 'Lucario', 'Mega Lucario', 'Machamp',
    'Mamoswine', 'Meganium', 'Meowscarada', 'Meowth',
    'Metagross', 'Mew', 'Mega Mewtwo X', 'Mega Mewtwo Y',
    'Mimikyu', 'Miraidon', 'Moltres', 'Mr. Mime',
    'Ninetales', 'Palkia', 'Pawmot', 'Pikachu',
    'Psyduck', 'Quaquaval', 'Raichu', 'Rapidash',
    'Sableye', 'Scizor', 'Scyther', "Sirfetch'd",
    'Skeledirge', 'Slowbro', 'Snorlax', 'Suicune',
    'Sylveon', 'Talonflame', 'Tinkaton', 'Trevenant',
    'Tsareena', 'Typhlosion', 'Tyranitar', 'Umbreon',
    'Urshifu', 'Vaporeon', 'Venusaur', 'Wigglytuff',
    'Yveltal', 'Zacian', 'Zapdos', 'Zeraora',
    'Zoroark',
]

# 一度取得したリストはプロセス存続中キャッシュする。再起動で再取得
_cached_names = None


async def fetch_pokemons():
    # ライブ JSON を取得し display_name のリストを返す。失敗は握りつぶさず送出する
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(POKEMON_JSON_URL, ssl=ssl_context) as response:
            response.raise_for_status()
            data = await response.json()
    names = [pokemon["display_name"] for pokemon in data]
    if not names:
        raise ValueError("ポケモンリストが空です")
    return names


async def get_pokemons():
    # キャッシュ優先。未取得ならライブ取得を試み、失敗時はフォールバックを返す
    global _cached_names
    if _cached_names is not None:
        return _cached_names
    try:
        _cached_names = await fetch_pokemons()
    except Exception as e:
        # 取得失敗を握りつぶさずログに残し、機能は固定リストで継続する
        print(f"⚠️ ポケモンリストのライブ取得に失敗: {e}")
        _cached_names = list(FALLBACK_POKEMONS)
    return _cached_names


def pick_pokemons(names, count):
    # 重複なしで count 体を抽選する。母数より多い指定は母数に丸める
    return random.sample(names, min(count, len(names)))
