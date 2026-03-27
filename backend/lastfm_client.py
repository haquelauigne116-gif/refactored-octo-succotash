"""
lastfm_client.py — Last.fm API 轻量客户端

获取歌曲/歌手的社区标签，用于辅助 AI 生成更准确的音乐分类标签。
"""
import logging
import requests
from typing import Optional

from backend.config import load_lastfm_config  # type: ignore[import]

logger = logging.getLogger(__name__)

_API_BASE = "https://ws.audioscrobbler.com/2.0/"
_TIMEOUT = 8

_cfg = load_lastfm_config()
_API_KEY = _cfg.get("api_key", "")


def _call(method: str, params: dict) -> Optional[dict]:
    """发送 Last.fm API 请求，返回 JSON 或 None。"""
    if not _API_KEY:
        logger.warning("[LastFM] API key 未配置，跳过")
        return None
    try:
        params.update({
            "method": method,
            "api_key": _API_KEY,
            "format": "json",
        })
        resp = requests.get(_API_BASE, params=params, timeout=_TIMEOUT)
        if resp.status_code != 200:
            logger.warning(f"[LastFM] HTTP {resp.status_code}: {resp.text[:200]}")
            return None
        data = resp.json()
        if "error" in data:
            logger.info(f"[LastFM] API 错误: {data.get('message', '')}")
            return None
        return data
    except Exception as e:
        logger.warning(f"[LastFM] 请求失败: {e}")
        return None


def get_track_tags(
    artist: str, track: str, min_count: int = 1, max_tags: int = 25,
) -> list[str]:
    """获取歌曲级别的社区标签。

    Args:
        artist: 歌手/乐队名
        track: 歌曲名
        min_count: 最低 count 阈值，过滤掉低质量标签
        max_tags: 最多返回多少个标签

    Returns:
        标签字符串列表，按热度降序排列
    """
    if not artist or not track:
        return []

    data = _call("track.getTopTags", {"artist": artist, "track": track})
    if not data:
        return []

    tags_raw = data.get("toptags", {}).get("tag", [])
    if isinstance(tags_raw, dict):
        tags_raw = [tags_raw]

    result: list[str] = []
    for t in tags_raw:
        name = str(t.get("name", "")).strip()
        count = int(t.get("count", 0))
        if name and count >= min_count:
            result.append(name)
        if len(result) >= max_tags:
            break

    logger.info(f"[LastFM] track.getTopTags({artist} - {track}): {len(result)} 个标签")
    return result


def get_artist_tags(
    artist: str, max_tags: int = 15,
) -> list[str]:
    """获取歌手级别的社区标签（作为 fallback）。

    Args:
        artist: 歌手/乐队名
        max_tags: 最多返回多少个标签

    Returns:
        标签字符串列表
    """
    if not artist:
        return []

    data = _call("artist.getTopTags", {"artist": artist})
    if not data:
        return []

    tags_raw = data.get("toptags", {}).get("tag", [])
    if isinstance(tags_raw, dict):
        tags_raw = [tags_raw]

    result: list[str] = []
    for t in tags_raw:
        name = str(t.get("name", "")).strip()
        count = int(t.get("count", 0))
        if name and count >= 1:
            result.append(name)
        if len(result) >= max_tags:
            break

    logger.info(f"[LastFM] artist.getTopTags({artist}): {len(result)} 个标签")
    return result


def get_music_tags(artist: str, track: str) -> list[str]:
    """获取音乐标签：优先 track 级别，无结果则 fallback 到 artist 级别。

    这是对外的统一入口。
    """
    tags = get_track_tags(artist, track)
    if not tags and artist:
        logger.info(f"[LastFM] track 无标签，尝试 artist fallback: {artist}")
        tags = get_artist_tags(artist)

    return tags
