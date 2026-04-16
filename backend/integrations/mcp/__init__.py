"""
mcp — MCP 协议客户端和工具管理

- client: MCP 连接管理（SSE / Streamable HTTP / stdio）
- manager: 工具注册、路由、降级逻辑
"""
from .manager import mcp_mgr, MCPManager  # noqa: F401
