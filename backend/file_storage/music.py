"""
music.py — 音乐特有功能

封面提取、歌词提取、AI 歌单生成、音频过滤。
"""
import hashlib
import json
import logging
import os

from backend.config import APP_SETTINGS, get_client  # type: ignore[import]

logger = logging.getLogger(__name__)

_AUDIO_CONTENT_TYPES = {
    "audio/mpeg", "audio/mp3", "audio/wav", "audio/flac",
    "audio/ogg", "audio/aac", "audio/x-m4a", "audio/mp4",
}

_AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".aac", ".m4a", ".wma"}


def is_audio(content_type: str, filename: str) -> bool:
    """判断是否为音频文件"""
    if content_type and content_type.lower() in _AUDIO_CONTENT_TYPES:
        return True
    ext = os.path.splitext(filename)[1].lower()
    return ext in _AUDIO_EXTENSIONS


def filter_audio(all_files: list[dict]) -> list[dict]:
    """从文件列表中过滤出音频文件"""
    return [
        f
        for f in all_files
        if is_audio(f.get("content_type", ""), f.get("original_name", ""))
    ]


def get_cover_art_file(object_name: str, storage) -> str | None:
    """获取本地缓存的封面路径，如果不存在则从 MinIO 下载并提取"""
    if not storage.enabled:
        return None

    covers_dir = os.path.join("data", "covers")
    temp_dir = os.path.join("data", "temp")
    os.makedirs(covers_dir, exist_ok=True)
    os.makedirs(temp_dir, exist_ok=True)

    safe_name = hashlib.md5(object_name.encode("utf-8")).hexdigest() + ".jpg"
    cover_path = os.path.join(covers_dir, safe_name)

    if os.path.exists(cover_path):
        return cover_path

    if not is_audio("", object_name):
        return None

    temp_path = os.path.join(temp_dir, "tmp_" + safe_name)
    try:
        storage.fget_object(object_name, temp_path)

        import mutagen  # type: ignore[import]

        audio = mutagen.File(temp_path)
        cover_data = None

        if audio is not None:
            ext = os.path.splitext(object_name)[1].lower()
            if ext == ".mp3":
                for key in audio.tags or {}:
                    if str(key).startswith("APIC"):
                        cover_data = audio.tags[key].data
                        break
            elif ext in (".m4a", ".mp4", ".aac"):
                covr = audio.get("covr")
                if covr and len(covr) > 0:
                    cover_data = bytes(covr[0])
            else:
                if hasattr(audio, "pictures") and audio.pictures:
                    cover_data = audio.pictures[0].data

        if cover_data:
            with open(cover_path, "wb") as f:
                f.write(cover_data)
            return cover_path
        else:
            return None
    except Exception as e:
        logger.warning(f"[CoverArt] 提取封面失败 {object_name}: {e}")
        return None
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def get_lyrics(object_name: str, storage) -> str:
    """获取本地缓存的歌词，如果不存在则从 MinIO 下载并提取"""
    if not storage.enabled:
        return ""

    lyrics_dir = os.path.join("data", "lyrics")
    temp_dir = os.path.join("data", "temp")
    os.makedirs(lyrics_dir, exist_ok=True)
    os.makedirs(temp_dir, exist_ok=True)

    safe_name = hashlib.md5(object_name.encode("utf-8")).hexdigest() + ".lrc"
    lyrics_path = os.path.join(lyrics_dir, safe_name)

    if os.path.exists(lyrics_path):
        with open(lyrics_path, "r", encoding="utf-8") as f:
            return f.read()

    if not is_audio("", object_name):
        return ""

    temp_path = os.path.join(temp_dir, "tmp_lyric_" + safe_name)
    lyrics_text = ""
    try:
        storage.fget_object(object_name, temp_path)

        import mutagen  # type: ignore[import]

        audio = mutagen.File(temp_path)
        if audio is not None:
            ext = os.path.splitext(object_name)[1].lower()
            if ext == ".mp3":
                for key in audio.tags or {}:
                    if str(key).startswith("USLT"):
                        lyrics_text = str(audio.tags[key])
                        break
            elif ext in (".m4a", ".mp4", ".aac"):
                lyr = audio.get("\xa9lyr")
                if lyr:
                    lyrics_text = (
                        str(lyr[0]) if isinstance(lyr, list) else str(lyr)
                    )
            else:
                lyr = audio.get("lyrics") or audio.get("LYRICS")
                if lyr:
                    lyrics_text = (
                        str(lyr[0]) if isinstance(lyr, list) else str(lyr)
                    )

        if lyrics_text and lyrics_text.strip():
            with open(lyrics_path, "w", encoding="utf-8") as f:
                f.write(lyrics_text.strip())
            return lyrics_text.strip()
        return ""
    except Exception as e:
        logger.warning(f"[Lyrics] 提取歌词失败 {object_name}: {e}")
        return ""
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def reindex_cover_art(all_audio: list[dict], index, storage) -> int:
    """重新提取所有音频的封面信息并更新索引，返回更新数量"""
    updated = 0
    for f in all_audio:
        object_name = f["object_name"]
        file_meta = f.get("file_meta", {})
        if file_meta.get("cover_art"):
            continue  # 已有封面标记
        cover_path = get_cover_art_file(object_name, storage)
        if cover_path:
            index.update_field(object_name, "file_meta.cover_art", True)
            updated += 1
            logger.info(f"[CoverArt] 已索引封面: {object_name}")
    return updated


def _classify_intent(prompt: str) -> list[str]:
    """Stage 0: 判断用户需求涉及哪些标签类别，减少传给 Stage 1 的标签池体积"""
    try:
        provider = APP_SETTINGS.get("summary_provider", "deepseek")
        model = APP_SETTINGS.get("summary_model", "deepseek-chat")
        ai_client = get_client(provider)

        resp = ai_client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一个意图分类助手。用户会输入一个音乐推荐需求，"
                        "你需要判断这个需求涉及以下哪些标签类别：\n\n"
                        "- author: 用户提到了特定歌手、乐队或创作者\n"
                        "- file_type: 用户要求特定音频格式（如无损、FLAC）\n"
                        "- location: 用户提到了语种、地区（如中文歌、日语、欧美）\n"
                        "- description: 用户描述了风格、心情、场景（如伤感、运动、摇滚）\n\n"
                        "只返回涉及的类别名称数组，严格按 JSON 格式，不要输出其他内容：\n"
                        '["description","author"]'
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.1,
            stream=False,
            max_tokens=50,
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        categories = json.loads(raw)
        valid = [c for c in categories if c in ("author", "file_type", "location", "description")]
        print(f"[Music] Stage0 意图分类: {valid}")
        return valid if valid else ["description"]
    except Exception as e:
        logger.error(f"[Music] Stage0 意图分类失败: {e}")
        return ["author", "description", "location"]


def _select_tags_from_pool(
    prompt: str, tag_pool: dict[str, list[str]],
) -> dict[str, dict[str, float]]:
    """Stage 1: AI 从音乐标签池中选出匹配标签并打分（0.1-1.0）"""
    pool_str = json.dumps(tag_pool, ensure_ascii=False)

    # 构建类别说明（仅包含传入的类别）
    cat_desc_map = {
        "author": "- author: 歌手/乐队/创作者",
        "file_type": "- file_type: 音频格式/类型",
        "location": "- location: 地区/语种",
        "description": "- description: 流派/风格/心情/场景",
    }
    cats_desc = "\n".join(cat_desc_map[c] for c in tag_pool.keys() if c in cat_desc_map)
    output_format = json.dumps({c: {"标签名": 0.8} for c in tag_pool.keys()}, ensure_ascii=False)

    try:
        provider = APP_SETTINGS.get("summary_provider", "deepseek")
        model = APP_SETTINGS.get("summary_model", "deepseek-chat")
        ai_client = get_client(provider)

        resp = ai_client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一个音乐推荐助手。用户会告诉你一个心情、场景或偏好，"
                        "你需要从下方音乐标签池中选出所有相关的标签，并给每个标签打一个相关性得分（0.1-1.0）。\n\n"
                        "得分说明：\n"
                        "- 1.0: 完全匹配用户需求（如用户说'伤感'，标签就是'伤感'）\n"
                        "- 0.7-0.9: 高度相关（如用户说'伤感'，标签是'ballad'或'深情'）\n"
                        "- 0.4-0.6: 中度相关（如用户说'伤感'，标签是'安静'或'夜晚'）\n"
                        "- 0.1-0.3: 弱相关\n\n"
                        f"标签池按以下类别组织：\n{cats_desc}\n\n"
                        "你需要从标签池中选出与用户需求相关的标签并打分。\n\n"
                        f"严格按以下 JSON 格式返回，不要输出其他任何内容：\n{output_format}"
                    ),
                },
                {
                    "role": "user",
                    "content": f"用户需求：{prompt}\n\n音乐标签池：\n{pool_str}",
                },
            ],
            temperature=0.3,
            stream=False,
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        result = json.loads(raw)
        # 清理：去掉示例占位符
        for cat in list(result.keys()):
            if isinstance(result[cat], dict):
                result[cat].pop("标签名", None)
        print(f"[Music] Stage1 标签选择(含得分): {result}")
        return result
    except Exception as e:
        logger.error(f"[Music] Stage1 标签选择失败: {e}")
        return {}


def _score_candidates(
    audio_files: list[dict],
    selected_tags: dict[str, dict[str, float]],
    max_candidates: int = 30,
) -> list[dict]:
    """Stage 1.5: 用选中的标签+得分对所有歌曲加权打分，返回 Top N 候选"""
    cat_weights = {"author": 25, "file_type": 5, "location": 10, "description": 15}
    scored: list[tuple[float, dict]] = []

    for f in audio_files:
        score = 0.0
        file_cats = f.get("categorized_tags", {})
        if not file_cats:
            # fallback: 用扁平 tags 匹配
            flat_tags = set(t.lower() for t in f.get("tags", []))
            for cat_tags in selected_tags.values():
                if isinstance(cat_tags, dict):
                    for tag, tag_score in cat_tags.items():
                        if tag.lower() in flat_tags:
                            score += 10.0 * float(tag_score)
        else:
            for cat, weight in cat_weights.items():
                cat_tags = selected_tags.get(cat, {})
                if not isinstance(cat_tags, dict):
                    continue
                file_tag_set = set(t.lower() for t in file_cats.get(cat, []))
                for search_tag, tag_score in cat_tags.items():
                    st_lower = search_tag.lower()
                    if st_lower in file_tag_set or any(
                        st_lower in ft for ft in file_tag_set
                    ):
                        score += weight * float(tag_score)

        if score > 0:
            scored.append((score, f))

    scored.sort(key=lambda x: x[0], reverse=True)
    candidates = [f for _, f in scored[:max_candidates]]
    print(f"[Music] Stage1.5 候选打分: {len(scored)} 首匹配, 取 Top {len(candidates)}")
    return candidates


def generate_playlist(
    prompt: str,
    audio_files: list[dict],
    get_url_fn,
    music_tag_pool: dict[str, list[str]] | None = None,
) -> dict:
    """AI 智能生成歌单：三阶段 — 意图分类 → 标签筛选 → AI 精选"""
    if not audio_files:
        return {
            "playlist_name": "空歌单",
            "description": "MinIO 中暂无音频文件，请先上传一些音乐。",
            "songs": [],
        }

    # ── Stage 0 + 1 + 1.5: 标签筛选（仅当歌曲数量较多时启用）──
    candidates = audio_files
    if len(audio_files) > 30 and music_tag_pool:
        # Stage 0: 判断需要哪些类别
        relevant_cats = _classify_intent(prompt)
        # 裁剪标签池，只保留相关类别
        trimmed_pool = {c: tags for c, tags in music_tag_pool.items() if c in relevant_cats}
        if not trimmed_pool:
            trimmed_pool = {"description": music_tag_pool.get("description", [])}
        print(f"[Music] Stage0 裁剪标签池: {list(trimmed_pool.keys())} "
              f"(标签数: {sum(len(v) for v in trimmed_pool.values())})")

        # Stage 1: AI 从裁剪后的标签池中选标签
        selected_tags = _select_tags_from_pool(prompt, trimmed_pool)
        if selected_tags:
            candidates = _score_candidates(audio_files, selected_tags)
            if not candidates:
                print("[Music] 标签筛选无候选，fallback 到全部歌曲")
                candidates = audio_files
    elif len(audio_files) > 30:
        print(f"[Music] 无音乐标签池，直接取前 50 首")
        candidates = audio_files[:50]

    # ── Stage 2: AI 从候选中精选排序 ──
    songs_info = "\n".join(
        f"{i+1}. 文件名: {f['original_name']} | "
        f"标签: {', '.join(f.get('tags', []))} | "
        f"描述: {f.get('description', '无')}"
        for i, f in enumerate(candidates)
    )
    print(f"[Music] Stage2 传入 {len(candidates)} 首候选给 AI 精选")

    try:
        provider = APP_SETTINGS.get("summary_provider", "deepseek")
        model = APP_SETTINGS.get("summary_model", "deepseek-chat")
        ai_client = get_client(provider)

        resp = ai_client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一个音乐歌单生成助手。用户会告诉你一个心情、场景或偏好，"
                        "你需要从下面的音频文件列表中挑选适合的歌曲并排序。\n\n"
                        "请严格按以下 JSON 格式返回，不要输出其他任何内容：\n"
                        '{"playlist_name": "歌单名称(简短有创意)", '
                        '"description": "一句话描述这个歌单的氛围", '
                        '"selected": [1, 3, 5]}\n\n'
                        "其中 selected 是你挑选的歌曲编号列表（按推荐顺序）。\n"
                        "如果所有歌曲都适合，可以全部选上。如果都不太匹配也没关系，"
                        "尽量挑最接近的，至少选 1 首。"
                    ),
                },
                {
                    "role": "user",
                    "content": f"用户需求：{prompt}\n\n可用歌曲列表：\n{songs_info}",
                },
            ],
            temperature=0.5,
            stream=False,
        )
        raw = resp.choices[0].message.content.strip()

        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        result = json.loads(raw)
        selected_indices = result.get("selected", [])

        songs = []
        for idx in selected_indices:
            real_idx = idx - 1
            if 0 <= real_idx < len(candidates):
                f = candidates[real_idx]
                f["download_url"] = get_url_fn(f["object_name"])
                songs.append(f)

        return {
            "playlist_name": result.get("playlist_name", "AI 歌单"),
            "description": result.get("description", "为你精心挑选的歌曲"),
            "songs": songs,
        }

    except Exception as e:
        logger.error(f"[Music] AI 歌单生成失败: {e}")
        for f in candidates:
            f["download_url"] = get_url_fn(f["object_name"])
        return {
            "playlist_name": "全部音乐",
            "description": "AI 歌单生成失败，已列出所有音频文件",
            "songs": candidates,
        }

