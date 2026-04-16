"""
manager.py — MCP 工具管理器

统一管理所有 MCP 工具的注册、路由、降级逻辑。
组合使用：
  - mcp.client: MCP 协议连接
  - remote_services: 远程服务工具定义和执行
  - media.downloader: 媒体下载处理
"""
from backend.config import APP_SETTINGS  # type: ignore[import]
from backend.integrations.mcp.client import MCPClient  # type: ignore[import]
from backend.integrations.remote_services.bailian_endpoints import MCP_ENDPOINTS  # type: ignore[import]
from backend.integrations.remote_services.bing_search import (  # type: ignore[import]
    bing_search, BING_SEARCH_TOOL_DEF,
)
from backend.integrations.remote_services.volcengine_jimeng import (  # type: ignore[import]
    execute_jimeng, JIMENG_TOOL_DEFS,
)
from backend.integrations.media.downloader import process_media_downloads  # type: ignore[import]


class MCPManager:
    """MCP 工具管理器：工具发现、路由、执行、降级。"""

    def __init__(self):
        pass

    # ====== 工具列表获取 ======

    async def _fetch_tools_async(self, intent: str) -> list[dict]:
        endpoint = MCP_ENDPOINTS.get(intent)
        if not endpoint:
            return []

        # 内置工具：直接返回预定义列表
        if endpoint.get("type") == "builtin":
            return self._get_builtin_tools(intent)

        # 远程工具：通过 MCP 协议获取
        async def _list(session) -> list[dict]:
            tools_response = await session.list_tools()
            openai_tools = []
            for t in tools_response.tools:
                cleaned_params = (
                    MCPClient.clean_json_schema(t.inputSchema)
                    if t.inputSchema
                    else {"type": "object", "properties": {}}
                )
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": cleaned_params,
                    }
                })
            print(f"[MCP] 成功获取 {intent} 提供的工具: {[t['function']['name'] for t in openai_tools]}")
            return openai_tools

        result = await MCPClient.connect_and_run(endpoint, _list, intent)
        if result is not None:
            return result

        # 远程失败 → 降级为内置
        builtin_fallback = self._get_builtin_tools(intent)
        if builtin_fallback:
            print(f"[MCP] 远程服务不可用，已降级为内置工具 ({intent})")
            return builtin_fallback
        return []

    async def _execute_tool_async(
        self, intent: str, tool_name: str, args: dict,
        session_id: str = "", session_dir: str = "",
    ) -> str:
        endpoint = MCP_ENDPOINTS.get(intent)
        if not endpoint:
            return "MCP 端点不存在或不支持此意图"

        # 内置工具：直接执行
        if endpoint.get("type") == "builtin":
            out_text = await self._execute_builtin(intent, tool_name, args)
            if session_id and session_dir and ("http://" in out_text or "https://" in out_text):
                out_text = await process_media_downloads(out_text, session_id, session_dir)
            return out_text

        # 远程工具：通过 MCP 协议调用
        async def _call(session) -> str:
            print(f"[MCP] 开始执行工具 {tool_name}，参数: {args}")
            tool_result = await session.call_tool(tool_name, arguments=args)

            if tool_result.content:
                out = []
                for c in tool_result.content:
                    if hasattr(c, "text"):
                        out.append(getattr(c, "text"))
                    else:
                        out.append(str(c))
                out_text = "\n".join(out)
            else:
                out_text = str(tool_result)

            if session_id and session_dir and ("http://" in out_text or "https://" in out_text):
                nonlocal self
                out_text = await process_media_downloads(out_text, session_id, session_dir)

            return out_text

        result = await MCPClient.connect_and_run(endpoint, _call, intent)
        if result is not None:
            return result

        # 远程失败 → 降级内置
        try:
            builtin_result = await self._execute_builtin(intent, tool_name, args)
            if not builtin_result.startswith("Error: 未知的内置工具"):
                print(f"[MCP] 远程工具执行失败，已降级为内置实现 ({intent}/{tool_name})")
                if session_id and session_dir and ("http://" in builtin_result or "https://" in builtin_result):
                    builtin_result = await process_media_downloads(builtin_result, session_id, session_dir)
                return builtin_result
        except Exception as fallback_err:
            print(f"[MCP] 内置工具降级也失败 ({tool_name}): {fallback_err}")

        return f"Error: 工具执行失败 ({tool_name})"

    # ====== 同步公开 API ======

    def get_tools_for_intent(self, intent: str) -> list[dict]:
        """同步获取适用于特定意图的 Tools"""
        if not APP_SETTINGS.get("enable_mcp_for_chat", False):
            return []
        if not intent or intent == "NONE":
            return []
        return MCPClient.run_async(self._fetch_tools_async(intent))

    def get_all_builtin_tools(self) -> list[dict]:
        """同步获取所有有内置实现的 MCP 工具。"""
        if not APP_SETTINGS.get("enable_mcp_for_chat", False):
            return []
        all_tools = []
        for intent_key in MCP_ENDPOINTS:
            try:
                builtin = self._get_builtin_tools(intent_key)
                if builtin:
                    all_tools.extend(builtin)
            except Exception:
                pass
        return all_tools

    def get_all_tools_for_loop(self, detected_intent: str) -> list[dict]:
        """获取循环所需的所有工具：核心内置工具 + 检测到的远程意图工具。"""
        if not APP_SETTINGS.get("enable_mcp_for_chat", False):
            return []
        tools = []
        loaded_names: set[str] = set()

        # 1. 始终加载核心意图工具
        for core_intent in ["JIMENG", "Z_IMAGE", "WEB_SEARCH"]:
            try:
                intent_tools = self.get_tools_for_intent(core_intent)
                for t in intent_tools:
                    fname = t["function"]["name"]
                    if fname not in loaded_names:
                        tools.append(t)
                        loaded_names.add(fname)
            except Exception as e:
                print(f"[MCP] 加载核心工具 {core_intent} 失败: {e}")

        # 2. 如果检测到额外意图，也加载它
        if detected_intent and detected_intent != "NONE" and detected_intent not in ("JIMENG", "WEB_SEARCH"):
            try:
                remote_tools = self.get_tools_for_intent(detected_intent)
                for t in remote_tools:
                    fname = t["function"]["name"]
                    if fname not in loaded_names:
                        tools.append(t)
                        loaded_names.add(fname)
            except Exception as e:
                print(f"[MCP] 加载远程工具 {detected_intent} 失败: {e}")
        return tools

    def execute_tool(self, intent: str, tool_name: str, args: dict,
                     session_id: str = "", session_dir: str = "") -> str:
        """同步执行特定意图下的工具调用"""
        return MCPClient.run_async(
            self._execute_tool_async(intent, tool_name, args, session_id, session_dir)
        )

    # ====== 内置工具注册 ======

    def _get_builtin_tools(self, intent: str) -> list[dict]:
        """返回内置工具的 OpenAI function 定义。"""
        if intent == "WEB_SEARCH":
            print(f"[MCP] 内置工具已就绪 (WEB_SEARCH): ['web_search']")
            return [BING_SEARCH_TOOL_DEF]

        if intent == "JIMENG":
            print(f"[MCP] 内置工具已就绪 (JIMENG): ['jimeng_image_generation', 'jimeng_video_generation']")
            return list(JIMENG_TOOL_DEFS)

        return []

    async def _execute_builtin(self, intent: str, tool_name: str, args: dict) -> str:
        """执行内置工具。"""
        if intent == "WEB_SEARCH" and tool_name == "web_search":
            query = args.get("query", "")
            max_results = args.get("max_results", 8)
            return await bing_search(query, max_results)

        if intent == "JIMENG":
            return await execute_jimeng(tool_name, args)

        return f"Error: 未知的内置工具 {tool_name}"

    # 兼容旧接口
    async def _process_media_downloads(self, text: str, session_id: str, session_dir: str) -> str:
        return await process_media_downloads(text, session_id, session_dir)

    async def _process_image_downloads(self, text: str, session_id: str, session_dir: str) -> str:
        return await process_media_downloads(text, session_id, session_dir)


# 单例
mcp_mgr = MCPManager()
