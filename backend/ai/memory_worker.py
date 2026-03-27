"""
memory_worker.py — 每日定时记忆整理 (两阶段: 逐会话提取 → 合并去重)
"""
import os
import re
import json
import logging
from datetime import datetime, timedelta

from backend.config import (  # type: ignore[import]
    MEMORY_FILE, SESSION_DIR, SESSIONS_META_FILE,
    get_client, APP_SETTINGS,
)

logger = logging.getLogger(__name__)


class DailyMemoryWorker:
    """
    每日凌晨运行的记忆整理 worker。

    Phase 1: 逐个会话提取关键事实
    Phase 2: 合并所有提取 + 现有 memory.md → AI 去重整理 → 覆盖 memory.md
    """

    def __init__(self):
        pass

    def run(self):
        """执行一次记忆整理（由 APScheduler cron 触发）"""
        logger.info("[MemoryWorker] ===== 开始每日记忆整理 =====")

        try:
            # 1. 找到需要处理的会话
            sessions_to_process = self._find_active_sessions()
            if not sessions_to_process:
                logger.info("[MemoryWorker] 没有需要处理的会话，跳过")
                return

            logger.info(f"[MemoryWorker] 找到 {len(sessions_to_process)} 个待处理会话")

            # 2. Phase 1: 逐会话提取
            extractions: list[str] = []
            for sid, info in sessions_to_process:
                result = self._extract_from_session(sid)
                if result:
                    extractions.append(result)

            if not extractions:
                logger.info("[MemoryWorker] 所有会话均无有价值记忆，跳过合并")
                self._mark_extracted(sessions_to_process)
                return

            # 3. Phase 2: 合并去重
            self._merge_and_write(extractions)

            # 4. 更新 memory_extracted_at
            self._mark_extracted(sessions_to_process)

            logger.info("[MemoryWorker] ===== 记忆整理完成 =====")

        except Exception as e:
            logger.error(f"[MemoryWorker] 整理过程异常: {e}", exc_info=True)

    def _find_active_sessions(self) -> list[tuple[str, dict]]:
        """找到需要提炼的会话：has_messages 且 (从未提炼 OR 提炼后有新活动)"""
        if not os.path.exists(SESSIONS_META_FILE):
            return []

        try:
            with open(SESSIONS_META_FILE, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            return []

        result = []
        for sid, info in meta.items():
            if not info.get("has_messages", False):
                continue

            extracted_at = info.get("memory_extracted_at")
            last_active = info.get("last_active", "")

            if not extracted_at:
                # 从未提炼过
                result.append((sid, info))
            elif last_active > extracted_at:
                # 提炼后又有新活动
                result.append((sid, info))

        return result

    def _extract_from_session(self, session_id: str) -> str:
        """Phase 1: 从单个会话文件中提取关键事实"""
        filepath = os.path.join(SESSION_DIR, session_id, "chat.md")
        if not os.path.exists(filepath):
            return ""

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.error(f"[MemoryWorker] 读取会话 {session_id} 失败: {e}")
            return ""

        # 跳过内容太少的会话
        if len(content.strip()) < 100:
            return ""

        # 截取最多 3000 字符（防止 token 爆炸）
        if len(content) > 3000:
            content = content[-3000:]  # type: ignore[index]

        try:
            client = get_client(APP_SETTINGS.get("summary_provider", "deepseek"))
            model = APP_SETTINGS.get("summary_model", "deepseek-chat")
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是一个记忆提取助手。请从以下对话中提取关于用户的重要信息，包括：\n"
                            "- 身份信息（姓名、年龄、职业、学校等）\n"
                            "- 个人偏好（喜好、习惯、风格偏好）\n"
                            "- 重要事件或约定\n"
                            "- 任何值得长期记住的事实\n\n"
                            "如果对话中没有这类信息，只回复「无」。\n"
                            "有的话请用简洁的条目列出，每条用 - 开头。"
                        ),
                    },
                    {"role": "user", "content": f"对话内容：\n{content}"},
                ],
                temperature=0.3,
                stream=False,
            )
            result: str = resp.choices[0].message.content.strip()
            if result == "无" or not result:
                return ""
            logger.info(f"[MemoryWorker] 会话 {session_id} 提取到记忆:\n{result[:200]}")  # type: ignore[index]
            return result

        except Exception as e:
            logger.error(f"[MemoryWorker] 会话 {session_id} AI 提取失败: {e}")
            return ""

    def _merge_and_write(self, extractions: list[str]):
        """Phase 2: 合并所有提取结果 + 现有 memory.md → 去重覆盖"""
        # 读取现有记忆
        existing_memory = ""
        if os.path.exists(MEMORY_FILE):
            try:
                with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                    existing_memory = f.read().strip()
            except Exception:
                pass

        # 拼接所有待合并内容
        all_parts = []
        if existing_memory and existing_memory != "# 长期记忆":
            all_parts.append(f"【已有记忆】\n{existing_memory}")  # type: ignore[arg-type]
        for i, ext in enumerate(extractions, 1):
            all_parts.append(f"【新提取 #{i}】\n{ext}")  # type: ignore[arg-type]

        combined = "\n\n".join(all_parts)

        try:
            client = get_client(APP_SETTINGS.get("summary_provider", "deepseek"))
            model = APP_SETTINGS.get("summary_model", "deepseek-chat")
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是一个记忆整理助手。请将以下多段用户记忆合并为一份统一的长期记忆档案：\n\n"
                            "要求：\n"
                            "1. 去除重复信息（同一事实只保留一条）\n"
                            "2. 矛盾信息以最新提取的为准\n"
                            "3. 按主题分类整理（身份信息 / 偏好 / 重要事项 等）\n"
                            "4. 每个分类用 ## 标题，每条记忆用 - 开头\n"
                            "5. 保持简洁，不要添加解释性文字\n\n"
                            "直接输出整理后的内容，不要有其他说明。"
                        ),
                    },
                    {"role": "user", "content": combined},
                ],
                temperature=0.2,
                stream=False,
            )
            merged = resp.choices[0].message.content.strip()

            # 写入 memory.md
            with open(MEMORY_FILE, "w", encoding="utf-8") as f:
                f.write(f"# 长期记忆\n\n{merged}\n")

            logger.info(f"[MemoryWorker] memory.md 已更新 ({len(merged)} 字)")

        except Exception as e:
            logger.error(f"[MemoryWorker] 合并记忆失败: {e}")

    def _mark_extracted(self, sessions: list[tuple[str, dict]]):
        """更新 _meta.json 中的 memory_extracted_at 时间戳"""
        if not os.path.exists(SESSIONS_META_FILE):
            return

        try:
            with open(SESSIONS_META_FILE, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            return

        now_str = datetime.now().isoformat()
        for sid, _ in sessions:
            if sid in meta:
                meta[sid]["memory_extracted_at"] = now_str

        try:
            with open(SESSIONS_META_FILE, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            logger.info(f"[MemoryWorker] 已标记 {len(sessions)} 个会话的提取时间")
        except Exception as e:
            logger.error(f"[MemoryWorker] 更新 meta 失败: {e}")
