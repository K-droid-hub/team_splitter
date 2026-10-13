import asyncio
import dataclasses

import yt_dlp

# 一度のコマンドでキューに積める最大数。プレイリスト URL で大量に流し込まれるのを防ぐ
MAX_RESOLVE_ITEMS = 50

# 長い曲の途中で回線が切れても ffmpeg 側で張り直させる。
# -vn は映像を捨てて音声だけを送るため
FFMPEG_BEFORE_OPTIONS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
FFMPEG_OPTIONS = "-vn"

_BASE_OPTIONS = {
    "quiet": True,
    "no_warnings": True,
    "format": "bestaudio/best",
    # URL でない入力は検索語として扱い、先頭 1 件だけ採用する
    "default_search": "ytsearch1",
    # IPv6 だけで出ようとして弾かれる環境があるため IPv4 を明示する
    "source_address": "0.0.0.0",
}


@dataclasses.dataclass
class Track:
    title: str
    webpage_url: str
    duration: int = None
    requested_by: str = None


def format_duration(seconds):
    # 長さ不明の配信などは None で返ってくるので、その旨を出す
    if not seconds:
        return "--:--"
    minutes, second = divmod(int(seconds), 60)
    hour, minute = divmod(minutes, 60)
    if hour:
        return f"{hour}:{minute:02d}:{second:02d}"
    return f"{minute}:{second:02d}"


def _to_track(entry, requested_by):
    # extract_flat では webpage_url が無く url に動画 URL が入ることがある
    url = entry.get("webpage_url") or entry.get("url")
    if not url:
        return None
    return Track(
        title=entry.get("title") or url,
        webpage_url=url,
        duration=entry.get("duration"),
        requested_by=requested_by,
    )


def _extract_tracks(query, requested_by):
    # プレイリストは件数が多いと解決に時間がかかるので、一覧だけ浅く取る
    options = dict(
        _BASE_OPTIONS, extract_flat="in_playlist", playlistend=MAX_RESOLVE_ITEMS
    )
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(query, download=False)
    if info is None:
        return []
    entries = info.get("entries")
    if entries is None:
        entries = [info]
    tracks = [_to_track(e, requested_by) for e in entries if e]
    return [t for t in tracks if t is not None][:MAX_RESOLVE_ITEMS]


def _extract_stream_url(webpage_url):
    with yt_dlp.YoutubeDL(dict(_BASE_OPTIONS, noplaylist=True)) as ydl:
        info = ydl.extract_info(webpage_url, download=False)
    if info is None:
        raise RuntimeError(f"音源を取得できませんでした: {webpage_url}")
    # 検索語を直接渡した場合は entries 側に入る
    entries = info.get("entries")
    if entries:
        info = entries[0]
    url = info.get("url")
    if not url:
        raise RuntimeError(f"再生可能な音声が見つかりませんでした: {webpage_url}")
    return url


# yt-dlp は同期 I/O なので、別スレッドへ逃がして bot の応答を止めない
async def resolve_query(query, requested_by=None):
    return await asyncio.to_thread(_extract_tracks, query, requested_by)


# ストリーム URL には有効期限があるため、キューから取り出す直前に解決する
async def resolve_stream_url(track):
    return await asyncio.to_thread(_extract_stream_url, track.webpage_url)
