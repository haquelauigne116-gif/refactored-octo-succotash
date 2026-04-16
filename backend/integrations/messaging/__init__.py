"""
messaging — 消息与通知

- dingtalk_handler: 钉钉消息处理
- notification_manager: 通知通道管理
"""
from .dingtalk_handler import DingTalkChatHandler  # noqa: F401
from .notification_manager import (  # noqa: F401
    NotificationManager, NotificationChannel,
    WebSocketChannel, DingTalkChannel,
)
