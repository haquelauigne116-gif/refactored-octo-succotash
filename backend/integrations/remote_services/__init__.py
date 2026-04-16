"""
remote_services — 远程 API 服务封装

- volcengine_jimeng: 火山引擎即梦 AI 图片/视频生成
- bing_search: Bing 搜索引擎
- bailian_endpoints: 阿里云百炼 MCP 端点配置
"""
from .volcengine_jimeng import jimeng_service, JimengService  # noqa: F401
from .bing_search import bing_search  # noqa: F401
from .bailian_endpoints import MCP_ENDPOINTS  # noqa: F401
