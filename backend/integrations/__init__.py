"""
integrations — 外部服务集成包

子包结构：
  - mcp/           : MCP 协议客户端与工具管理
  - remote_services/: 远程 API 服务封装（火山引擎即梦、Bing、阿里云百炼）
  - media/          : 媒体下载处理
  - messaging/      : 消息通知（钉钉、WebSocket）
  - music/          : 音乐元信息（Last.fm、元数据搜索）

所有导出保持向后兼容，旧的导入路径继续有效。
"""

# ── MCP ──
from .mcp import mcp_mgr, MCPManager  # noqa: F401

# ── 远程服务 ──
from .remote_services import jimeng_service, JimengService  # noqa: F401

# ── 消息通知 ──
from .messaging import (  # noqa: F401
    DingTalkChatHandler,
    NotificationManager, NotificationChannel,
    WebSocketChannel, DingTalkChannel,
)

# ── 音乐 ──
from .music import get_music_tags, search_music_metadata  # noqa: F401

__all__ = [
    "mcp_mgr", "MCPManager",
    "jimeng_service", "JimengService",
    "DingTalkChatHandler",
    "NotificationManager", "NotificationChannel",
    "WebSocketChannel", "DingTalkChannel",
    "get_music_tags",
    "search_music_metadata",
]
