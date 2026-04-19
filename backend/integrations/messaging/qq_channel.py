"""
qq_channel.py — QQ 通知通道 (基于 NapCat / OneBot 11 HTTP API)

通过 NapCat 的 HTTP 接口向 QQ 好友或群聊推送消息。
"""
import json
import logging
import time
from typing import Optional

import requests  # type: ignore[import]

from backend.integrations.messaging.notification_manager import (  # type: ignore[import]
    NotificationChannel,
)

logger = logging.getLogger(__name__)

# QQ 单条消息合理上限（CQ 文本），留安全余量
_QQ_MAX_CHARS = 4000


class QQChannel(NotificationChannel):
    """QQ 机器人通道 — 基于 NapCat (OneBot 11) HTTP API"""

    name = "qq"

    def __init__(
        self,
        napcat_http_url: str = "http://127.0.0.1:3000",
        napcat_token: str = "",
        msg_type: str = "private",          # "private" | "group"
        target_user_ids: Optional[list[int]] = None,
        target_group_ids: Optional[list[int]] = None,
        enabled: bool = False,
    ):
        self.enabled = enabled
        self.napcat_http_url = napcat_http_url.rstrip("/")
        self.napcat_token = napcat_token
        self.msg_type = msg_type
        self.target_user_ids: list[int] = target_user_ids or []
        self.target_group_ids: list[int] = target_group_ids or []

    # ========== NotificationChannel 接口 ==========

    def send(self, data: dict) -> bool:
        """发送通知（推送到 QQ）"""
        if not self.enabled:
            return False

        content = self._format_message(data)
        try:
            if self.msg_type == "group":
                return self._send_group(content)
            else:
                return self._send_private(content)
        except Exception as e:
            logger.error(f"[QQ] 发送异常: {e}")
            return False

    # ========== 消息分片 ==========

    @staticmethod
    def _split_message(content: str, max_len: int = _QQ_MAX_CHARS) -> list[str]:
        """将超长消息按段落拆分为多条，每条不超过 max_len"""
        if len(content) <= max_len:
            return [content]

        chunks: list[str] = []
        remaining = content

        while remaining:
            if len(remaining) <= max_len:
                chunks.append(remaining)
                break

            cut = remaining[:max_len].rfind("\n")
            if cut <= 0:
                cut = max_len

            chunk = remaining[:cut].rstrip()
            remaining = remaining[cut:].lstrip("\n")
            chunks.append(chunk)

        if len(chunks) > 1:
            total = len(chunks)
            chunks = [f"({i}/{total})\n{c}" for i, c in enumerate(chunks, 1)]

        return chunks

    # ========== 私聊发送 ==========

    def _send_private(self, content: str) -> bool:
        """发送私聊消息给所有目标用户，支持自动分片"""
        # 运行时重新加载 user_ids（支持动态注册）
        try:
            from backend.config import load_notification_config  # type: ignore[import]
            fresh_cfg = load_notification_config()
            fresh_ids = fresh_cfg.get("channels", {}).get("qq", {}).get("target_user_ids", [])
            if fresh_ids:
                self.target_user_ids = fresh_ids
        except Exception:
            pass

        if not self.target_user_ids:
            logger.warning("[QQ] 未配置 target_user_ids，无法发送私聊消息")
            return False

        chunks = self._split_message(content)
        all_ok = True
        for uid in self.target_user_ids:
            for chunk in chunks:
                if not self._post_send_msg("private", user_id=uid, message=chunk):
                    all_ok = False
        return all_ok

    # ========== 群聊发送 ==========

    def _send_group(self, content: str) -> bool:
        """发送群聊消息到所有目标群，支持自动分片"""
        if not self.target_group_ids:
            logger.warning("[QQ] 未配置 target_group_ids，无法发送群聊消息")
            return False

        chunks = self._split_message(content)
        all_ok = True
        for gid in self.target_group_ids:
            for chunk in chunks:
                if not self._post_send_msg("group", group_id=gid, message=chunk):
                    all_ok = False
        return all_ok

    # ========== HTTP API 调用 ==========

    def _post_send_msg(
        self,
        message_type: str,
        message: str,
        user_id: int = 0,
        group_id: int = 0,
    ) -> bool:
        """调用 OneBot 11 /send_msg 端点"""
        url = f"{self.napcat_http_url}/send_msg"
        headers = {"Content-Type": "application/json"}
        if self.napcat_token:
            headers["Authorization"] = f"Bearer {self.napcat_token}"

        payload: dict = {
            "message_type": message_type,
            "message": message,
        }
        if message_type == "private":
            payload["user_id"] = user_id
        else:
            payload["group_id"] = group_id

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=10)
            result = resp.json()
            if result.get("status") == "ok" or result.get("retcode") == 0:
                logger.info(f"[QQ] {message_type}消息发送成功 (target={user_id or group_id})")
                return True
            else:
                logger.error(f"[QQ] 发送失败: {result}")
                return False
        except Exception as e:
            logger.error(f"[QQ] HTTP 请求异常: {e}")
            return False

    # ========== 测试连通性 ==========

    def test_send(self) -> bool:
        """发送一条测试消息"""
        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        content = (
            "🔔 QQ 通道测试\n\n"
            f"连接状态: ✅ 正常\n"
            f"测试时间: {now_str}\n\n"
            "如果你看到这条消息，说明 QQ 推送已成功配置！"
        )
        try:
            if self.msg_type == "group":
                return self._send_group(content)
            else:
                return self._send_private(content)
        except Exception as e:
            logger.error(f"[QQ] 测试发送失败: {e}")
            return False

    # ========== 消息格式化 ==========

    @staticmethod
    def _clean_thinking_chain(text: str) -> str:
        """清理 AI 回复中的思考链标记（复用钉钉通道逻辑）"""
        import re as _re

        if not text:
            return text

        lines = text.split("\n")
        cleaned: list[str] = []
        skip_until_blank = False

        for line in lines:
            stripped = line.strip()

            if _re.match(r'\*{0,2}Plan[（(]规划[）)]', stripped, _re.IGNORECASE):
                skip_until_blank = True
                continue
            if _re.match(r'\*{0,2}Observe[（(]观察[）)]', stripped, _re.IGNORECASE):
                skip_until_blank = True
                continue
            if _re.match(r'\*{0,2}最终回复\*{0,2}\s*[:：]?\s*$', stripped):
                skip_until_blank = False
                continue
            if stripped.startswith("💡 分析") or stripped.startswith("💡 分析"):
                continue
            if stripped.startswith("🔍 Observe"):
                continue

            if skip_until_blank:
                if not stripped:
                    skip_until_blank = False
                continue

            cleaned.append(line)

        result = "\n".join(cleaned).strip()
        result = _re.sub(r'\n{3,}', '\n\n', result)
        return result

    def _format_message(self, data: dict) -> str:
        """将通知数据格式化为文本消息"""
        msg_type = data.get("type")
        if msg_type in ["schedule_reminder", "daily_briefing"]:
            return data.get("result", "")

        task_name = data.get("task_name", "未知任务")
        result = data.get("result", "")
        time_str = data.get("time", "")

        result = self._clean_thinking_chain(result)

        return f"📋 定时任务提醒\n\n🏷 任务: {task_name}\n🕐 时间: {time_str}\n\n{result}"
