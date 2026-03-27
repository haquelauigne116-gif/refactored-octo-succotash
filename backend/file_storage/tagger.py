"""
tagger.py — AI 标签生成

负责从文件元信息 → 分类标签，包含 AI 调用和元信息 → 标签转换。
"""
import json
import logging

from backend.config import APP_SETTINGS, get_client  # type: ignore[import]

logger = logging.getLogger(__name__)


def metadata_to_categorized_tags(meta: dict) -> dict[str, list[str]]:
    """将提取的元信息转换为分类标签字典 (4 个类别)"""
    cats: dict[str, list[str]] = {
        "file_type": [],
        "author": [],
        "location": [],
        "description": [],
    }
    ft = meta.get("file_type", "")
    if ft:
        type_map = {"audio": "音频", "image": "图片", "video": "视频"}
        cats["file_type"].append(type_map.get(ft) or ft)
    if meta.get("format"):
        cats["file_type"].append(str(meta["format"]).upper())

    if meta.get("artists"):
        cats["author"].extend(str(a) for a in meta["artists"])
    if meta.get("camera"):
        cats["file_type"].append(str(meta["camera"]))

    if meta.get("location"):
        loc_parts = [
            p.strip() for p in str(meta["location"]).split(",") if p.strip()
        ]
        for i in range(min(3, len(loc_parts))):
            if len(loc_parts[i]) <= 15:
                cats["location"].append(str(loc_parts[i]))

    if meta.get("title"):
        cats["description"].append(str(meta["title"]))
    if meta.get("album"):
        cats["description"].append(str(meta["album"]))
    if meta.get("genre"):
        cats["description"].append(str(meta["genre"]))

    return cats


def flatten_categorized_tags(cats: dict[str, list[str]]) -> list[str]:
    """将分类标签合并为扁平列表 (兼容旧 tags 字段)"""
    seen: set[str] = set()
    flat: list[str] = []
    for tag_list in cats.values():
        for t in tag_list:
            if t.lower() not in seen:
                seen.add(t.lower())
                flat.append(t)
    return flat


def generate_categorized_tags(
    filename: str,
    description: str,
    file_meta: dict | None = None,
    lastfm_tags: list[str] | None = None,
) -> dict:
    """生成分类标签 + 提取文件日期

    返回 {file_type: [...], author: [...], location: [...], description: [...], file_date: "..."}
    """
    # 1. 从元信息提取硬标签
    meta_cats = (
        metadata_to_categorized_tags(file_meta)
        if file_meta
        else {"file_type": [], "author": [], "location": [], "description": []}
    )

    # 元信息中的 file_date
    meta_file_date = ""
    if file_meta and file_meta.get("taken_at"):
        meta_file_date = str(file_meta["taken_at"])[:10]

    try:
        provider = APP_SETTINGS.get("file_provider", "deepseek")
        model = APP_SETTINGS.get("file_model", "deepseek-chat")
        ai_client = get_client(provider)

        # 构建元信息上下文
        meta_context = ""
        if file_meta:
            parts: list[str] = []
            if file_meta.get("title"):
                parts.append("歌曲名: " + str(file_meta["title"]))
            if file_meta.get("artists"):
                parts.append(
                    "创作者: " + ", ".join(str(a) for a in file_meta["artists"])
                )
            if file_meta.get("album"):
                parts.append("专辑: " + str(file_meta["album"]))
            if file_meta.get("genre"):
                parts.append("风格: " + str(file_meta["genre"]))
            if file_meta.get("lyricist"):
                parts.append("作词: " + str(file_meta["lyricist"]))
            if file_meta.get("composer"):
                parts.append("作曲: " + str(file_meta["composer"]))
            if file_meta.get("camera"):
                parts.append("设备: " + str(file_meta["camera"]))
            if file_meta.get("location"):
                parts.append("拍摄地点: " + str(file_meta["location"]))
            if file_meta.get("taken_at"):
                parts.append("拍摄时间: " + str(file_meta["taken_at"]))
            if file_meta.get("lyrics"):
                lyrics_snippet = str(file_meta["lyrics"])[:200]
                parts.append("歌词片段: " + lyrics_snippet)
            if parts:
                meta_context = "\n文件元信息：" + "；".join(parts)

        # Last.fm 社区标签上下文
        if lastfm_tags:
            meta_context += "\nLast.fm 社区标签（需筛选，去除无效标签）：" + "、".join(lastfm_tags)

        resp = ai_client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一个文件标签生成助手。根据文件名、用户描述、文件元信息以及社区标签，按 4 个类别生成丰富详细的语义标签，并提取文件日期。\n\n"
                        "类别说明：\n"
                        "- file_type: 文件格式、媒体类型（如 音频、MP3、无损、流行音乐等）\n"
                        "- author: 创作者、艺术家、歌手、乐队、作曲、作词、摄影师\n"
                        "- location: 地点、城市、国家、语言（如果是音乐，可以写语种如国语、英语、韩语）\n"
                        "- description: 风格、心情、场景、主题、乐器、节奏等丰富的语义描述（不要包含任何时间/日期/年份信息）\n\n"
                        "特别要求：对于音乐/歌曲文件，请充分发挥联想，生成具体的音乐流派（如 R&B、摇滚、电子）、情感基调（如 治愈、伤感、高燃）、适合听的场景（如 运动、工作、深夜）以及特色乐器等。\n\n"
                        "如果输入中包含 Last.fm 社区标签，你需要从中筛选出有效的流派/风格/情绪/场景标签，"
                        "并去除无效标签（如 awesome、love、favourite、seen live、my favourite、beautiful、太棒了 等主观评价词，"
                        "以及纯粹是艺术家名字重复的标签）。将筛选后的有效标签融入到对应类别中。\n\n"
                        "file_date: 从所有信息中提取文件的创建/发行/拍摄日期，格式 YYYY-MM-DD。\n"
                        "如果只能确定年份就写 YYYY，年月就写 YYYY-MM，无法确定则留空字符串。\n\n"
                        "请尽可能多地提供相关且准确的详细标签（每个类别尽量生成 4-8 个简短标签）。如果某类别实在无相关信息可留空。\n"
                        "严格按以下 JSON 格式返回，不要输出其他任何内容：\n"
                        '{"file_type":["..."],"author":["..."],"location":["..."],"description":["..."],"file_date":""}'
                    ),
                },
                {
                    "role": "user",
                    "content": f"文件名：{filename}\n描述：{description or '无'}{meta_context}",
                },
            ],
            temperature=0.3,
            stream=False,
        )
        raw: str = resp.choices[0].message.content.strip()

        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        ai_result = json.loads(raw)

        # 2. 合并标签：元信息标签优先 + AI 标签去重补充
        merged: dict[str, list[str]] = {}
        for cat in ("file_type", "author", "location", "description"):
            seen: set[str] = set()
            result_list: list[str] = []
            for t in meta_cats.get(cat, []):
                if t and t.lower() not in seen:
                    seen.add(t.lower())
                    result_list.append(t)
            for t in ai_result.get(cat, []):
                t = str(t).strip()
                if t and t.lower() not in seen:
                    seen.add(t.lower())
                    result_list.append(t)
            merged[cat] = result_list[:8]

        # 3. file_date: 元信息优先
        ai_file_date = str(ai_result.get("file_date", "")).strip()
        merged["file_date"] = meta_file_date or ai_file_date  # type: ignore[assignment]

        return merged

    except Exception as e:
        logger.error(f"[Tagger] AI 分类标签生成失败: {e}")
        meta_cats["file_date"] = meta_file_date  # type: ignore[assignment]
        return meta_cats
