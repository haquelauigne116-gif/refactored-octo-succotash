"""
notification_manager.py — 多通道通知管理器 (WebSocket + QQ)
"""
import json
import re
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


# ========== 通知管理器 ==========

class NotificationManager:
    """管理所有通知通道，统一 dispatch"""

    def __init__(self):
        self.channels: list[NotificationChannel] = []

    def add_channel(self, channel: NotificationChannel):
        """注册一个通知通道"""
        self.channels.append(channel)

    def start(self):
        """启动所有通道"""
        for ch in self.channels:
            if ch.enabled:
                try:
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
