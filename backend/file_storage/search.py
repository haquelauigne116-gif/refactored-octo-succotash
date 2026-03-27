"""
search.py — 搜索逻辑

关键词搜索 + AI 语义搜索（从标签池匹配）。
"""
import json
import logging

from backend.config import APP_SETTINGS, get_client  # type: ignore[import]

logger = logging.getLogger(__name__)


def keyword_search(query: str, all_files: list[dict]) -> list[dict]:
    """关键词搜索（从本地索引，毫秒级响应，优先匹配 categorized_tags），输出 0-100 评分"""
    if not query.strip():
        return all_files

    q = query.lower()
    cat_weights = {"author": 25, "file_type": 15, "location": 10, "description": 5}
    scored: list[tuple[float, dict]] = []

    for f in all_files:
        score = 0.0
        name_lower = f.get("original_name", "").lower()
        desc_lower = f.get("description", "").lower()
        
        # 1. 精准名字匹配 (最高可得约 60分)
        if q == name_lower or q == name_lower.rsplit('.', 1)[0]:
            score += 60.0
        elif q in name_lower:
            score += 40.0
        else:
            words = q.split()
            match_w = sum(1 for w in words if w in name_lower)
            if match_w > 0:
                score += 20.0 * (match_w / len(words))
                
        # 2. 描述匹配加分 (最高可得约 20分)
        if q in desc_lower:
            score += 20.0
        elif any(w in desc_lower for w in q.split()):
            score += 10.0

        cats = f.get("categorized_tags", {})
        if cats:
            for cat, weight in cat_weights.items():
                for tag in cats.get(cat, []):
                    if q in tag.lower():
                        score += weight
        else:
            for tag in f.get("tags", []):
                if q in tag.lower():
                    score += 10.0
                    
        if score > 0:
            final_score = min(score, 99.0)
            f_copy = dict(f)
            f_copy["_score"] = round(final_score, 1)
            scored.append((final_score, f_copy))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [f for _, f in scored]


def ai_search(
    prompt: str,
    all_files: list[dict],
    tag_pool: dict[str, list[str]],
    get_url_fn,
) -> dict:
    """AI 智能检索文件：AI 从标签池中选 tags → 索引匹配 → 按关联性排序"""
    if not all_files:
        return {"status": "error", "message": "MinIO 中暂无文件", "files": []}

    pool_str = json.dumps(tag_pool, ensure_ascii=False)

    try:
        provider = APP_SETTINGS.get("file_provider", "deepseek")
        model = APP_SETTINGS.get("file_model", "deepseek-chat")
        ai_client = get_client(provider)

        resp = ai_client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一个智能文件检索助手。用户会输入搜索需求，你需要从下方标签池中选出相关标签来检索文件。\n\n"
                        "标签池按 4 个类别组织：\n"
                        "- author: 创作者/艺术家 (权重最高)\n"
                        "- file_type: 文件格式/媒体类型\n"
                        "- location: 地点/城市\n"
                        "- description: 风格/心情/场景/主题\n\n"
                        "你需要：\n"
                        "1. 从标签池中选出与用户需求相关的标签\n"
                        "2. 只选标签池中已有的标签（可以适当模糊匹配）\n"
                        "3. 如果用户需求侧重描述/风格，可通过 description_boost 提升某些 description 标签的权重\n"
                        "4. 如果用户提到了时间（如'去年的''2024年的''最近一个月'），用 time_range 输出大概的日期范围（格式 YYYY-MM-DD），程序会自动过滤\n\n"
                        "严格按以下 JSON 格式返回，不要输出其他任何内容：\n"
                        '{"search_tags":{"author":[],"file_type":[],"location":[],"description":[]},'
                        '"description_boost":{"标签名":1.5},'
                        '"time_range":{"start":"2024-01-01","end":"2024-12-31"},'
                        '"reason":"简短说明匹配思路"}\n'
                        "time_range 仅在用户提到时间时才填写，否则设为 null。"
                    ),
                },
                {
                    "role": "user",
                    "content": f"用户需求：{prompt}\n\n标签池：\n{pool_str}",
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
        search_tags: dict = result.get("search_tags", {})
        desc_boost: dict = result.get("description_boost", {})
        time_range: dict | None = result.get("time_range")
        reason = result.get("reason", "")

        # 用 search_tags 匹配文件并评分，增加基础文本匹配
        cat_weights = {"author": 25, "file_type": 15, "location": 10, "description": 5}
        scored: list[tuple[float, dict]] = []

        q_lower = prompt.lower()
        
        for f in all_files:
            score = 0.0
            matched_tags: list[str] = []
            name_lower = f.get("original_name", "").lower()
            desc_lower = f.get("description", "").lower()

            # 1. 基础关键字匹配 (即便 AI 标签没搜准，关键字也能保底)
            if q_lower == name_lower or q_lower == name_lower.rsplit('.', 1)[0]:
                score += 60.0
                matched_tags.append("精准命名")
            elif q_lower in name_lower:
                score += 40.0
                matched_tags.append("包含命名")
            else:
                words = q_lower.split()
                match_w = sum(1 for w in words if w in name_lower)
                if match_w > 0:
                    score += 20.0 * (match_w / len(words))
            
            if q_lower in desc_lower:
                score += 20.0

            # 2. AI 语义标签匹配
            file_cats = f.get("categorized_tags", {})
            if not file_cats:
                flat_tags = set(t.lower() for t in f.get("tags", []))
                all_search = []
                for tags_list in search_tags.values():
                    all_search.extend(tags_list)
                match_count = sum(
                    1 for t in all_search if t.lower() in flat_tags
                )
                if match_count > 0:
                    score += match_count * 10.0
            else:
                for cat, weight in cat_weights.items():
                    file_tag_set = set(t.lower() for t in file_cats.get(cat, []))
                    for search_tag in search_tags.get(cat, []):
                        st_lower = search_tag.lower()
                        if st_lower in file_tag_set or any(
                            st_lower in ft for ft in file_tag_set
                        ):
                            if cat == "description" and search_tag in desc_boost:
                                score += weight * float(desc_boost[search_tag])
                            else:
                                score += weight
                            matched_tags.append(search_tag)

            if score > 0:
                final_score = min(score, 98.0)  # 留出 100 分给时间范围内精准匹配的
                f_copy = dict(f)
                f_copy["_score"] = round(final_score, 1)
                f_copy["_matched_tags"] = matched_tags
                scored.append((final_score, f_copy))

        # 时间范围过滤
        if time_range and isinstance(time_range, dict):
            t_start = time_range.get("start", "")
            t_end = time_range.get("end", "")
            if t_start or t_end:
                filtered: list[tuple[float, dict]] = []
                for s, f_item in scored:
                    fd = f_item.get("file_date", "")
                    if not fd:
                        filtered.append((s, f_item))
                        continue
                    if t_start and fd < t_start:
                        continue
                    if t_end and fd > t_end:
                        continue
                    new_score = min(s + 15.0, 100.0)
                    f_item["_score"] = round(new_score, 1)
                    filtered.append((new_score, f_item))
                scored = filtered

        # 按评分排序
        scored.sort(key=lambda x: x[0], reverse=True)
        files = []
        for _, f in scored[:20]:
            f["download_url"] = get_url_fn(
                f["object_name"],
                force_download=True,
                filename=f["original_name"],
            )
            files.append(f)

        return {
            "status": "ok",
            "reason": reason,
            "search_tags": search_tags,
            "files": files,
        }

    except Exception as e:
        logger.error(f"[Search] AI 文件检索失败: {e}")
        return {"status": "error", "message": f"AI 检索失败: {e}", "files": []}
