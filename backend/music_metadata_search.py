"""
music_metadata_search.py — 联网搜索音乐元信息 + 歌词

上传音乐时：
1. 先检查音频文件是否已有内嵌歌词
2. 如果没有，通过 NeteaseCloudMusicApi 搜索歌词并写入音频文件
3. 通过 MCP 搜索补充元信息（专辑、年份、作词作曲）
4. 返回所有元信息（含歌词）供 AI 标签生成使用

歌词搜索：NeteaseCloudMusicApi (localhost:3000)
元信息搜索：MCP 智谱搜索 (zhipu-websearch)
"""
import os
import re
import json
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

# NeteaseCloudMusicApi 配置
_NETEASE_API = "http://localhost:3000"
_NETEASE_TIMEOUT = 10


def _clean_filename_for_search(filename: str) -> str:
    """从文件名中猜测歌曲标题（去掉扩展名、序号、噪声词等）"""
    name = os.path.splitext(filename)[0]
    name = re.sub(r'^(\d{1,3}[\.\-\s_]+)', '', name)
    name = re.sub(r'^(Track\s*\d+[\.\-\s_]*)', '', name, flags=re.IGNORECASE)
    name = re.sub(
        r'[\(\[](official|video|audio|lyrics|mv|hd|hq|\d+kbps|flac|mp3)[\)\]]',
        '', name, flags=re.IGNORECASE
    )
    name = name.replace('_', ' ')
    name = re.sub(r'\s+', ' ', name).strip()
    return name


# ─── 歌词读取 / 写入（mutagen） ───

def _read_embedded_lyrics(file_path: str) -> str:
    """从音频文件读取已嵌入的歌词，返回歌词文本或空字符串。"""
    try:
        import mutagen  # type: ignore[import]
    except ImportError:
        return ""

    ext = os.path.splitext(file_path)[1].lower()
    try:
        audio = mutagen.File(file_path)
        if audio is None:
            return ""

        if ext == ".mp3":
            if audio.tags:
                for key in audio.tags:
                    if key.startswith("USLT"):
                        lyrics = str(audio.tags[key])
                        if lyrics.strip():
                            return lyrics.strip()

        elif ext in (".flac", ".ogg"):
            lyrics_list = audio.get("LYRICS") or audio.get("lyrics") or []
            if lyrics_list:
                lyrics = str(lyrics_list[0]) if isinstance(lyrics_list, list) else str(lyrics_list)
                if lyrics.strip():
                    return lyrics.strip()

        elif ext in (".m4a", ".aac", ".mp4"):
            if audio.tags:
                lyrics_list = audio.tags.get("\xa9lyr", [])
                if lyrics_list:
                    lyrics = str(lyrics_list[0])
                    if lyrics.strip():
                        return lyrics.strip()

    except Exception as e:
        logger.warning(f"[MusicSearch] 读取内嵌歌词失败: {e}")
    return ""


def _write_lyrics_to_file(file_path: str, lyrics_text: str) -> bool:
    """将歌词嵌入音频文件的标签中（直接写入，不生成额外文件）。"""
    try:
        from mutagen.mp3 import MP3  # type: ignore[import]
        from mutagen.flac import FLAC  # type: ignore[import]
        from mutagen.oggvorbis import OggVorbis  # type: ignore[import]
        from mutagen.mp4 import MP4  # type: ignore[import]
        from mutagen.id3 import USLT  # type: ignore[import]
    except ImportError:
        logger.warning("[MusicSearch] mutagen 未安装，无法写入歌词")
        return False

    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".mp3":
            audio = MP3(file_path)
            if audio.tags is None:
                audio.add_tags()
            audio.tags.delall("USLT")
            audio.tags.add(USLT(encoding=3, lang="zho", desc="", text=lyrics_text))
            audio.save()
            return True

        elif ext == ".flac":
            audio = FLAC(file_path)
            audio["LYRICS"] = lyrics_text
            audio.save()
            return True

        elif ext == ".ogg":
            audio = OggVorbis(file_path)
            audio["LYRICS"] = lyrics_text
            audio.save()
            return True

        elif ext in (".m4a", ".aac", ".mp4"):
            audio = MP4(file_path)
            if audio.tags is None:
                audio.add_tags()
            audio.tags["\xa9lyr"] = [lyrics_text]
            audio.save()
            return True

    except Exception as e:
        logger.warning(f"[MusicSearch] 写入歌词失败: {e}")
    return False


# ─── NeteaseCloudMusicApi 歌词搜索 ───

def _netease_api_available() -> bool:
    """检查 NeteaseCloudMusicApi 是否可用。"""
    try:
        r = requests.get(_NETEASE_API, timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _netease_search_song(keyword: str) -> Optional[tuple]:
    """通过关键词搜索歌曲，返回 (song_id, song_name, artist_name) 或 None。"""
    try:
        res = requests.get(
            f"{_NETEASE_API}/search",
            params={"keywords": keyword, "type": 1, "limit": 1},
            timeout=_NETEASE_TIMEOUT,
        )
        songs = res.json().get("result", {}).get("songs")
        if songs:
            song = songs[0]
            return (
                song["id"],
                song.get("name", ""),
                song.get("artists", [{}])[0].get("name", ""),
            )
    except Exception as e:
        logger.warning(f"[MusicSearch] 网易云搜索失败: {e}")
    return None


def _netease_get_lyrics(song_id: int) -> Optional[str]:
    """通过歌曲 ID 获取歌词，保留 LRC 时间标签。"""
    try:
        res = requests.get(
            f"{_NETEASE_API}/lyric",
            params={"id": song_id},
            timeout=_NETEASE_TIMEOUT,
        )
        data = res.json()

        if data.get("nolyric") or data.get("uncollected"):
            return None

        lrc = data.get("lrc", {}).get("lyric", "")
        if not lrc:
            return None

        # 保留 LRC 时间标签，清理并过滤空行
        lines = []
        for line in lrc.split("\n"):
            clean = line.strip()
            if clean:
                lines.append(clean)

        return "\n".join(lines) if lines else None

    except Exception as e:
        logger.warning(f"[MusicSearch] 网易云歌词获取失败: {e}")
    return None


def _search_lyrics_netease(title: str, artist: str) -> Optional[str]:
    """通过网易云音乐搜索歌词，返回纯文本歌词或 None。"""
    if not _netease_api_available():
        logger.info("[MusicSearch] NeteaseCloudMusicApi 不可用，跳过歌词搜索")
        return None

    keyword = f"{artist} {title}".strip() if artist else title
    logger.info(f"[MusicSearch] 网易云搜索: {keyword}")

    result = _netease_search_song(keyword)
    if not result:
        logger.info("[MusicSearch] 网易云搜索无结果")
        return None

    song_id, match_name, match_artist = result
    logger.info(f"[MusicSearch] 网易云匹配: {match_artist} - {match_name} (ID: {song_id})")

    lyrics = _netease_get_lyrics(song_id)
    if lyrics:
        logger.info(f"[MusicSearch] 获取到歌词 ({len(lyrics)} 字)")
    else:
        logger.info("[MusicSearch] 该歌曲无歌词")

    return lyrics


# ─── MCP 搜索（元信息补充） ───

def _mcp_search(query: str) -> list[dict]:
    """通过 MCP 智谱搜索执行联网搜索，返回结构化结果列表。"""
    try:
        from backend.mcp_manager import mcp_mgr  # type: ignore[import]
    except ImportError:
        logger.warning("[MusicSearch] 无法导入 mcp_manager")
        return []

    try:
        raw = mcp_mgr.execute_tool(
            "WEB_SEARCH", "webSearchStd",
            {"search_query": query, "count": 5},
        )
        if not raw or raw.startswith("Error"):
            return []

        # MCP 返回的可能是双重编码的 JSON（字符串包裹的 JSON）
        parsed = json.loads(raw)
        # 如果第一次 decode 结果是字符串，再 decode 一次
        if isinstance(parsed, str):
            parsed = json.loads(parsed)

        return parsed if isinstance(parsed, list) else []

    except Exception as e:
        logger.warning(f"[MusicSearch] MCP 搜索异常: {e}")
        return []


def _extract_metadata_from_results(results: list[dict]) -> dict:
    """从 MCP 搜索结果中提取音乐元信息。"""
    meta: dict = {}
    all_content = "\n".join(
        f"{r.get('title', '')}\n{r.get('content', '')}" for r in results
    )
    if not all_content.strip():
        return {}

    # 专辑
    for pat in [
        r'收录[于在].*?(?:发行的)?专辑[《【]([^》】]+)[》】]',
        r'专辑[《【]([^》】]+)[》】]',
    ]:
        m = re.search(pat, all_content)
        if m and not meta.get("album"):
            meta["album"] = m.group(1).strip()

    # 发行日期
    for pat in [
        r'(\d{4})年(\d{1,2})月(\d{1,2})日.*?发行',
        r'发行[于在].*?(\d{4})年(\d{1,2})月(\d{1,2})日',
    ]:
        m = re.search(pat, all_content)
        if m and not meta.get("year"):
            meta["year"] = m.group(1)
            try:
                meta["release_date"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            except (ValueError, IndexError):
                pass
            break

    # 作词 / 作曲 — 匹配 "作词：周杰伦" "词：周杰伦" 等格式
    # 值必须看起来像人名（主要是中文字符，2-10字）
    for pat in [
        r'作词\s*[：:]\s*([\u4e00-\u9fff]{2,10})',
        r'填词\s*[：:]\s*([\u4e00-\u9fff]{2,10})',
        r'词\s*[：:]\s*([\u4e00-\u9fff]{2,10})',
    ]:
        m = re.search(pat, all_content)
        if m and not meta.get("lyricist"):
            meta["lyricist"] = m.group(1).strip()
            break

    for pat in [
        r'作曲\s*[：:]\s*([\u4e00-\u9fff]{2,10})',
        r'谱曲\s*[：:]\s*([\u4e00-\u9fff]{2,10})',
        r'曲\s*[：:]\s*([\u4e00-\u9fff]{2,10})',
    ]:
        m = re.search(pat, all_content)
        if m and not meta.get("composer"):
            meta["composer"] = m.group(1).strip()
            break

    return meta


# ─── 公开 API ───

def search_music_metadata(
    title: str = "",
    artists: list[str] | None = None,
    album: str = "",
    filename: str = "",
    file_path: str = "",
) -> dict:
    """
    联网搜索音乐元信息 + 歌词。

    流程:
    1. 检查文件是否已有内嵌歌词
    2. 没有歌词 → NeteaseCloudMusicApi 搜索歌词 → 写入音频文件
    3. MCP 搜索补充元信息（专辑、年份、作词作曲）
    4. 返回所有元信息

    Args:
        file_path: 音频文件路径（用于读写歌词）

    Returns:
        dict，可能包含: lyrics, album, year, lyricist, composer, release_date
    """
    result: dict = {}

    # 构建搜索关键词
    search_title = title.strip()
    search_artist = ""
    if artists:
        search_artist = artists[0].strip()

    if not search_title:
        search_title = _clean_filename_for_search(filename)
        if not search_title:
            logger.info("[MusicSearch] 无法确定搜索关键词，跳过联网搜索")
            return {}

    logger.info(f"[MusicSearch] 开始搜索: {search_title} - {search_artist}")

    # ── Step 1: 检查已有歌词 ──
    existing_lyrics = ""
    if file_path and os.path.isfile(file_path):
        existing_lyrics = _read_embedded_lyrics(file_path)
        if existing_lyrics:
            logger.info(f"[MusicSearch] 文件已有内嵌歌词 ({len(existing_lyrics)} 字)")
            result["lyrics"] = existing_lyrics

    # ── Step 2: 搜索歌词（如果文件中没有） ──
    if not existing_lyrics:
        lyrics = _search_lyrics_netease(search_title, search_artist)
        if lyrics:
            result["lyrics"] = lyrics
            # 写入音频文件
            if file_path and os.path.isfile(file_path):
                if _write_lyrics_to_file(file_path, lyrics):
                    logger.info("[MusicSearch] 歌词已写入音频文件")
                else:
                    logger.warning("[MusicSearch] 歌词写入失败")

    # ── Step 3: MCP 搜索补充元信息 ──
    query = f"{search_title} {search_artist} 歌曲 专辑 发行时间".strip()
    logger.info(f"[MusicSearch] MCP 搜索元信息: {query}")
    meta_results = _mcp_search(query)
    if meta_results:
        meta = _extract_metadata_from_results(meta_results)
        result.update(meta)
        logger.info(f"[MusicSearch] 元信息: {meta}")

    if result:
        result["source"] = "netease+mcp"
        logger.info(
            f"[MusicSearch] 最终结果: "
            f"{ {k: (v[:60] + '...' if isinstance(v, str) and len(v) > 60 else v) for k, v in result.items()} }"
        )

    return result
