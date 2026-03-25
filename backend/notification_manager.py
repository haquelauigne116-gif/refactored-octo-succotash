"""
notification_manager.py — 多通道通知管理器 (WebSocket + 钉钉 Stream)
"""
import json
import time
import threading
import logging
from abc import ABC, abstractmethod
from typing import Any, Optional, Callable

import requests  # type: ignore[import]

logger = logging.getLogger(__name__)


# ========== 抽象基类 ==========

class NotificationChannel(ABC):
    """通知通道抽象基类"""
    name: str = "base"
    enabled: bool = False

    @abstractmethod
    def send(self, data: dict) -> bool:
        """发送通知，返回是否成功"""
        ...

    def start(self):
        """启动通道（可选实现）"""
        pass

    def shutdown(self):
        """关闭通道（可选实现）"""
        pass


# ========== WebSocket 通道 ==========

class WebSocketChannel(NotificationChannel):
    """WebSocket 广播通道（复用现有 broadcast_sync）"""
    name = "websocket"

    def __init__(self):
        self.enabled = True
        self._broadcast_fn: Optional[Callable] = None

    def set_broadcast_fn(self, fn: Callable):
        """注入 broadcast_sync 函数"""
        self._broadcast_fn = fn

    def send(self, data: dict) -> bool:
        fn = self._broadcast_fn
        if fn is None:
            return False
        try:
            fn(data)
            return True
        except Exception as e:
            logger.error(f"[WebSocket] 推送失败: {e}")
            return False


# ========== 钉钉通道 ==========

class DingTalkChannel(NotificationChannel):
    """钉钉机器人通道 (OpenAPI + Stream)"""
    name = "dingtalk"

    def __init__(
        self,
        app_key: str = "",
        app_secret: str = "",
        agent_id: str = "",
        robot_code: str = "",
        open_conversation_id: str = "",
        user_ids: Optional[list[str]] = None,
        msg_type: str = "single",  # "single" | "group"
        enabled: bool = False,
    ):
        self.enabled = enabled
        self.app_key = app_key
        self.app_secret = app_secret
        self.agent_id = agent_id
        self.robot_code = robot_code or app_key  # robotCode 通常等于 appKey
        self.open_conversation_id = open_conversation_id
        self.user_ids: list[str] = user_ids or []  # 单聊目标用户ID列表
        self.msg_type = msg_type  # 消息类型：单聊或群聊

        # access_token 管理
        self._access_token: str = ""
        self._token_expires_at: float = 0

        # Stream 客户端
        self._stream_client: Any = None
        self._stream_thread: Optional[threading.Thread] = None

    def start(self, chat_handler: Any = None):
        """启动钉钉 Stream 连接，可注入外部聊天处理器"""
        if not self.enabled or not self.app_key or not self.app_secret:
            logger.info("[DingTalk] 未启用或缺少凭据，跳过启动")
            return

        try:
            from dingtalk_stream import Credential, DingTalkStreamClient  # type: ignore[import]
            from dingtalk_stream.chatbot import ChatbotMessage, ChatbotHandler  # type: ignore[import]

            credential = Credential(self.app_key, self.app_secret)
            self._stream_client = DingTalkStreamClient(credential)

            # 用外部 chat_handler（DingTalkChatHandler）处理消息
            _external_handler = chat_handler

            class _BotHandler(ChatbotHandler):
                async def process(self_inner, callback):  # type: ignore[override]
                    from dingtalk_stream import AckMessage  # type: ignore[import]
                    msg = ChatbotMessage.from_dict(callback.data)

                    if _external_handler is not None:
                        try:
                            await _external_handler.handle_message(
                                msg, self_inner.reply_text
                            )
                        except Exception as e:
                            logger.error(f"[DingTalk] 消息处理异常: {e}")
                    else:
                        sender = getattr(msg, 'sender_nick', '未知')
                        logger.info(f"[DingTalk] 收到来自 {sender} 的消息（无处理器）")

                    return AckMessage.STATUS_OK, 'OK'

            self._stream_client.register_callback_handler(
                ChatbotMessage.TOPIC,
                _BotHandler(),
            )

            # 在后台线程中运行 Stream 连接
            thread = threading.Thread(
                target=self._run_stream,
                daemon=True,
                name="dingtalk-stream",
            )
            self._stream_thread = thread
            thread.start()
            logger.info("[DingTalk] Stream 连接已在后台启动")
        except ImportError:
            logger.warning("[DingTalk] dingtalk-stream 未安装，跳过 Stream 连接 (pip install dingtalk-stream)")
        except Exception as e:
            logger.error(f"[DingTalk] Stream 启动失败: {e}")

    def _run_stream(self):
        """在后台线程中运行 Stream 事件循环（自动重连）"""
        client = self._stream_client
        if client is None:
            return
        try:
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            # 使用 start_forever 实现断线自动重连
            client.start_forever()
        except Exception as e:
            logger.error(f"[DingTalk] Stream 连接异常: {e}")

    def shutdown(self):
        """关闭 Stream 连接"""
        self._stream_client = None
        logger.info("[DingTalk] 已关闭")

    def send(self, data: dict) -> bool:
        """通过钉钉 OpenAPI 发送消息（支持单聊和群聊）"""
        if not self.enabled:
            return False

        content = self._format_message(data)

        try:
            token = self._get_access_token()
            if not token:
                return False

            if self.msg_type == "group":
                return self._send_group(token, content)
            else:
                return self._send_single(token, content)
        except Exception as e:
            logger.error(f"[DingTalk] 发送异常: {e}")
            return False

    def _send_single(self, token: str, content: str) -> bool:
        """发送单聊消息 (oToMessages/batchSend)"""
        # 每次发送前重新加载 user_ids（支持运行时自动注册新用户）
        try:
            from backend.config import load_notification_config  # type: ignore[import]
            fresh_cfg = load_notification_config()
            fresh_ids = fresh_cfg.get("channels", {}).get("dingtalk", {}).get("user_ids", [])
            if fresh_ids:
                self.user_ids = fresh_ids
        except Exception:
            pass

        if not self.user_ids:
            logger.warning("[DingTalk] 未配置 user_ids，无法发送单聊消息")
            return False

        resp = requests.post(
            "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend",
            headers={
                "x-acs-dingtalk-access-token": token,
                "Content-Type": "application/json",
            },
            json={
                "robotCode": self.robot_code,
                "userIds": self.user_ids,
                "msgKey": "sampleText",
                "msgParam": json.dumps({"content": content}, ensure_ascii=False),
            },
            timeout=10,
        )

        if resp.status_code == 200:
            logger.info("[DingTalk] 单聊消息发送成功")
            return True
        else:
            logger.error(f"[DingTalk] 单聊发送失败 ({resp.status_code}): {resp.text}")
            return False

    def _send_group(self, token: str, content: str) -> bool:
        """发送群聊消息 (groupMessages/send)"""
        if not self.open_conversation_id:
            logger.warning("[DingTalk] 未配置 open_conversation_id，无法发送群消息")
            return False

        resp = requests.post(
            "https://api.dingtalk.com/v1.0/robot/groupMessages/send",
            headers={
                "x-acs-dingtalk-access-token": token,
                "Content-Type": "application/json",
            },
            json={
                "robotCode": self.robot_code,
                "openConversationId": self.open_conversation_id,
                "msgKey": "sampleText",
                "msgParam": json.dumps({"content": content}, ensure_ascii=False),
            },
            timeout=10,
        )

        if resp.status_code == 200:
            logger.info("[DingTalk] 群聊消息发送成功")
            return True
        else:
            logger.error(f"[DingTalk] 群聊发送失败 ({resp.status_code}): {resp.text}")
            return False

    def test_send(self) -> bool:
        """发送一条测试消息，验证钉钉通道连通性"""
        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        content = f"🔔 钉钉通道测试\n\n连接状态: ✅ 正常\n测试时间: {now_str}\n\n如果你看到这条消息，说明钉钉推送已成功配置！"
        try:
            token = self._get_access_token()
            if not token:
                return False
            return self._send_single(token, content)
        except Exception as e:
            logger.error(f"[DingTalk] 测试发送失败: {e}")
            return False

    def _format_message(self, data: dict) -> str:
        """将通知数据格式化为文本消息"""
        msg_type = data.get("type")
        if msg_type in ["schedule_reminder", "daily_briefing"]:
            return data.get("result", "")

        task_name = data.get("task_name", "未知任务")
        result = data.get("result", "")
        time_str = data.get("time", "")
        return f"📋 定时任务提醒\n\n🏷 任务: {task_name}\n🕐 时间: {time_str}\n\n{result}"

    def _get_access_token(self) -> str:
        """获取或刷新 access_token"""
        now = time.time()
        if self._access_token and now < self._token_expires_at - 60:
            return self._access_token

        try:
            resp = requests.post(
                "https://api.dingtalk.com/v1.0/oauth2/accessToken",
                json={
                    "appKey": self.app_key,
                    "appSecret": self.app_secret,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                self._access_token = data["accessToken"]
                self._token_expires_at = now + data.get("expireIn", 7200)
                logger.info("[DingTalk] access_token 获取成功")
                return self._access_token
            else:
                logger.error(f"[DingTalk] 获取 token 失败 ({resp.status_code}): {resp.text}")
                return ""
        except Exception as e:
            logger.error(f"[DingTalk] 获取 token 异常: {e}")
            return ""


# ========== 通知管理器 ==========

class NotificationManager:
    """管理所有通知通道，统一 dispatch"""

    def __init__(self):
        self.channels: list[NotificationChannel] = []

    def add_channel(self, channel: NotificationChannel):
        """注册一个通知通道"""
        self.channels.append(channel)

    def start(self, chat_handler=None):
        """启动所有通道"""
        for ch in self.channels:
            if ch.enabled:
                try:
                    # 钉钉通道需要传入聊天处理器
                    if isinstance(ch, DingTalkChannel) and chat_handler is not None:
                        ch.start(chat_handler=chat_handler)
                    else:
                        ch.start()
                except Exception as e:
                    logger.error(f"[NotificationManager] 启动通道 {ch.name} 失败: {e}")
        enabled = [ch.name for ch in self.channels if ch.enabled]
        logger.info(f"[NotificationManager] 已启动通道: {enabled}")

    def shutdown(self):
        """关闭所有通道"""
        for ch in self.channels:
            try:
                ch.shutdown()
            except Exception:
                pass

    def dispatch(self, data: dict):
        """向所有已启用的通道推送消息"""
        for ch in self.channels:
            if ch.enabled:
                try:
                    ch.send(data)
                except Exception as e:
                    logger.error(f"[NotificationManager] 通道 {ch.name} 推送失败: {e}")

    def get_channel(self, name: str) -> Optional[NotificationChannel]:
        """按名称查找通道"""
        for ch in self.channels:
            if ch.name == name:
                return ch
        return None
