import asyncio

import pytest

import src.pokemons as pokemons
from src.pokemons import FALLBACK_POKEMONS, get_pokemons, pick_pokemons


@pytest.fixture(autouse=True)
def clear_pokemon_cache():
    # モジュールレベルのキャッシュがテスト間で漏れないようにする
    pokemons._cached_names = None
    yield
    pokemons._cached_names = None


def test_pick_pokemons_returns_requested_count():
    names = ["A", "B", "C", "D", "E"]
    assert len(pick_pokemons(names, 3)) == 3


def test_pick_pokemons_has_no_duplicates():
    names = ["A", "B", "C", "D", "E"]
    picks = pick_pokemons(names, 5)
    assert len(picks) == len(set(picks))


def test_pick_pokemons_caps_at_population_size():
    # 母数より多い指定は母数に丸める
    names = ["A", "B", "C"]
    assert len(pick_pokemons(names, 5)) == 3


def test_get_pokemons_falls_back_on_fetch_error(monkeypatch):
    async def raise_error():
        raise RuntimeError("network down")

    monkeypatch.setattr(pokemons, "fetch_pokemons", raise_error)
    result = asyncio.run(get_pokemons())
    assert result == FALLBACK_POKEMONS


def test_get_pokemons_caches_fetched_list(monkeypatch):
    call_count = {"n": 0}

    async def fake_fetch():
        call_count["n"] += 1
        return ["Pikachu", "Snorlax"]

    monkeypatch.setattr(pokemons, "fetch_pokemons", fake_fetch)

    async def scenario():
        first = await get_pokemons()
        second = await get_pokemons()
        return first, second

    first, second = asyncio.run(scenario())
    assert first == ["Pikachu", "Snorlax"]
    assert second == ["Pikachu", "Snorlax"]
    assert call_count["n"] == 1  # 2 回目はキャッシュから返る
