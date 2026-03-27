"""
integrations — 外部服务集成包

包含 MCP 工具管理、火山引擎、钉钉、通知管理、Last.fm、音乐元信息搜索。
"""
from .mcp_manager import mcp_mgr, MCPManager  # noqa: F401
from .volcengine_service import jimeng_service, JimengService  # noqa: F401
from .dingtalk_handler import DingTalkChatHandler  # noqa: F401
from .notification_manager import (  # noqa: F401
    NotificationManager, NotificationChannel,
    WebSocketChannel, DingTalkChannel,
)
from .lastfm_client import get_music_tags  # noqa: F401
from .music_metadata_search import search_music_metadata  # noqa: F401

__all__ = [
    "mcp_mgr", "MCPManager",
    "jimeng_service", "JimengService",
    "DingTalkChatHandler",
    "NotificationManager", "NotificationChannel",
    "WebSocketChannel", "DingTalkChannel",
    "get_music_tags",
    "search_music_metadata",
]
