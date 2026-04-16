"""
bailian_endpoints.py — 阿里云百炼 MCP 远程服务端点配置

管理所有通过阿里云百炼平台接入的 MCP 远程服务 URL。
type="remote"  → 百炼远程 SSE / Streamable HTTP（需要 bailian_api_key）
type="stdio"   → 本地 stdio 子进程（免费，无需 API Key）
type="builtin" → 内置实现（无需外部服务）
"""

# MCP 服务端点配置
MCP_ENDPOINTS: dict = {
    # ── 阿里云百炼远程 MCP 服务 ──
    "Z_IMAGE": {
        "type": "remote",
        "url": "https://dashscope.aliyuncs.com/api/v1/mcps/zimage/mcp",
        "description": "通义万相图片生成",
    },
    "AMAP": {
        "type": "remote",
        "url": "https://dashscope.aliyuncs.com/api/v1/mcps/amap-maps/mcp",
        "description": "高德地图 POI / 天气",
    },
    "WEB_SEARCH": {
        "type": "remote",
        "url": "https://dashscope.aliyuncs.com/api/v1/mcps/zhipu-websearch/sse",
        "description": "智谱联网搜索",
    },
    "TTS": {
        "type": "remote",
        "url": "https://dashscope.aliyuncs.com/api/v1/mcps/QwenTextToSpeech/mcp",
        "description": "Qwen 语音合成",
    },

    # ── 火山引擎即梦（内置 SDK 调用，非 MCP 协议） ──
    "JIMENG": {
        "type": "builtin",
        "description": "火山引擎即梦 AI 图片/视频生成",
    },
}
