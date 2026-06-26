from __future__ import annotations
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path("data")
HISTORY_FILE = DATA_DIR / "game_history.json"
WEIGHTS_FILE = DATA_DIR / "weights.json"


def _ensure_data_dir():
    DATA_DIR.mkdir(exist_ok=True)


def load_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    with HISTORY_FILE.open(encoding="utf-8") as f:
        return json.load(f)


def _save_history(history: list[dict]):
    _ensure_data_dir()
    with HISTORY_FILE.open("w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def add_game(
    guild_id: int,
    opponent_team: list[str],
    suggested_team: list[str],
    feature_scores: dict[str, float],
    weights_used: dict[str, float],
) -> str:
    history = load_history()
    game_id = str(uuid.uuid4())[:8]
    history.append({
        "id": game_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "guild_id": guild_id,
        "opponent_team": opponent_team,
        "suggested_team": suggested_team,
        "feature_scores": feature_scores,
        "weights_used": weights_used,
        "result": None,
    })
    _save_history(history)
    return game_id


def record_result(game_id: str, result: str) -> bool:
    """result は 'win' または 'loss'。マッチしたら True。"""
    history = load_history()
    for record in reversed(history):
        if record["id"] == game_id:
            record["result"] = result
            _save_history(history)
            return True
    return False


def load_weights() -> dict | None:
    if not WEIGHTS_FILE.exists():
        return None
    with WEIGHTS_FILE.open(encoding="utf-8") as f:
        return json.load(f)


def save_weights(weights: dict):
    _ensure_data_dir()
    with WEIGHTS_FILE.open("w", encoding="utf-8") as f:
        json.dump(weights, f, ensure_ascii=False, indent=2)
