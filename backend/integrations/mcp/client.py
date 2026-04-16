"""
client.py — MCP 协议连接管理

负责 MCP 客户端的底层连接：
  - 远程连接（SSE / Streamable HTTP，阿里云百炼）
  - 本地连接（stdio 子进程）
  - HTTP 预检
  - JSON Schema 清洗
  - 异步转同步执行器
"""
import asyncio
import shutil
import concurrent.futures

import httpx  # type: ignore[import]
from contextlib import AsyncExitStack
from mcp.client.session import ClientSession  # type: ignore[import]
from mcp.client.sse import sse_client  # type: ignore[import]
from mcp.client.streamable_http import streamable_http_client  # type: ignore[import]
from mcp.client.stdio import stdio_client  # type: ignore[import]
from mcp import StdioServerParameters  # type: ignore[import]

from backend.config import APP_SETTINGS  # type: ignore[import]


class MCPClient:
    """MCP 协议连接管理器，处理所有底层传输细节。"""

    @staticmethod
    def clean_json_schema(schema: dict) -> dict:
        """递归清理 Bailian 返回的非标准 JSON Schema 类型。
        将 bool -> boolean, int -> integer 等。"""
        if not isinstance(schema, dict):
            return schema

        type_mapping = {
            "bool": "boolean",
            "int": "integer",
            "str": "string",
            "dict": "object",
            "list": "array",
            "float": "number",
        }

        cleaned = {}
        for k, v in schema.items():
            if k == "type" and isinstance(v, str) and v in type_mapping:
                cleaned[k] = type_mapping[v]
            elif isinstance(v, dict):
                cleaned[k] = MCPClient.clean_json_schema(v)
            elif isinstance(v, list):
                cleaned[k] = [
                    MCPClient.clean_json_schema(item) if isinstance(item, dict) else item
                    for item in v
                ]
            else:
                cleaned[k] = v
        return cleaned

    @staticmethod
    async def preflight_check(url: str, api_key: str) -> tuple[bool, str]:
        """HTTP 预检请求，验证 MCP 服务可达性。返回 (通过, 诊断信息)。"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as check_client:
                method = "HEAD" if url.endswith("/sse") else "GET"
                resp = await check_client.request(
                    method, url,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                if resp.status_code == 401:
                    return False, "API Key 认证失败 (HTTP 401)，请检查 bailian_api_key"
                if resp.status_code == 403:
                    return False, "API Key 无权限 (HTTP 403)，请确认已开通 MCP 服务"
                if resp.status_code == 404:
                    return False, f"MCP 端点不存在 (HTTP 404): {url}"
                if resp.status_code >= 500:
                    return False, f"MCP 服务端内部错误 (HTTP {resp.status_code})"
                return True, f"预检通过 (HTTP {resp.status_code})"
        except httpx.ConnectError:
            return False, f"无法连接到 MCP 服务器: {url}"
        except httpx.TimeoutException:
            return False, f"连接 MCP 服务器超时: {url}"
        except Exception as e:
            print(f"[MCP] 预检异常 (非阻断): {e}")
            return True, f"预检异常但不阻断: {e}"

    @staticmethod
    async def connect_and_run(endpoint: dict, callback, intent: str = ""):
        """根据端点类型选择连接方式，然后执行 callback(session)。"""
        ep_type = endpoint.get("type", "remote")
        if ep_type == "stdio":
            return await MCPClient._run_stdio(endpoint, callback, intent)
        else:
            return await MCPClient._run_remote(endpoint, callback, intent)

    @staticmethod
    async def _run_stdio(endpoint: dict, callback, intent: str = ""):
        """通过 stdio 子进程连接本地 MCP 服务器。"""
        command = endpoint["command"]
        env = endpoint.get("env", {})

        resolved = shutil.which(command)
        if not resolved:
            import os, site
            user_scripts = os.path.join(site.getusersitepackages(), "..", "Scripts")
            user_scripts = os.path.abspath(user_scripts)
            candidate = os.path.join(user_scripts, command + ".exe")
            if os.path.exists(candidate):
                resolved = candidate
                print(f"[MCP] 在用户脚本目录找到命令: {resolved}")
            else:
                print(f"[MCP] ❌ 未找到命令 '{command}'")
                return None

        server_params = StdioServerParameters(command=resolved, env=env)

        max_retries = 1
        for attempt in range(max_retries + 1):
            try:
                async with AsyncExitStack() as stack:
                    read, write = await stack.enter_async_context(
                        stdio_client(server_params)
                    )
                    session = await stack.enter_async_context(ClientSession(read, write))
                    await session.initialize()
                    return await callback(session)
            except Exception as e:
                if attempt < max_retries:
                    print(f"[MCP] stdio 连接失败 (第 {attempt+1} 次)，重试... 原因: {e}")
                    await asyncio.sleep(1)
                    continue
                print(f"[MCP] stdio 执行失败 ({intent}): {e}")
                return None
        return None

    @staticmethod
    async def _run_remote(endpoint: dict, callback, intent: str = ""):
        """通过远程 SSE / Streamable HTTP 连接百炼 MCP 服务器。"""
        url = endpoint["url"]
        api_key = APP_SETTINGS.get("bailian_api_key", "").strip()
        if not api_key:
            print(f"[MCP] 未配置 bailian_api_key，无法加载 {intent}")
            return None

        ok, diag = await MCPClient.preflight_check(url, api_key)
        if not ok:
            print(f"[MCP] 预检失败 ({intent}): {diag}")
            return None
        print(f"[MCP] 预检通过 ({intent}): {diag}")

        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                async with AsyncExitStack() as stack:
                    if url.endswith("/sse"):
                        read, write = await stack.enter_async_context(
                            sse_client(url=url, headers={"Authorization": f"Bearer {api_key}"}, timeout=300.0)
                        )
                    else:
                        http_client = httpx.AsyncClient(
                            headers={"Authorization": f"Bearer {api_key}"}, timeout=300.0
                        )
                        await stack.enter_async_context(http_client)
                        result = await stack.enter_async_context(
                            streamable_http_client(url=url, http_client=http_client)
                        )
                        read, write = result[0], result[1]

                    session = await stack.enter_async_context(ClientSession(read, write))
                    await asyncio.wait_for(session.initialize(), timeout=30.0)
                    return await asyncio.wait_for(callback(session), timeout=60.0)
            except Exception as e:
                inner_msg = ""
                if hasattr(e, "exceptions"):
                    for sub_e in e.exceptions:  # type: ignore
                        inner_msg += f" | {repr(sub_e)}"

                is_retryable = "task_status.started" in str(e) or "task_status.started" in inner_msg

                if attempt < max_retries and is_retryable:
                    wait_sec = 2 * (attempt + 1)
                    print(f"[MCP] SSE 连接失败 (第 {attempt+1} 次)，{wait_sec}s 后重试...")
                    await asyncio.sleep(wait_sec)
                    continue

                print(f"[MCP] 获取工具列表失败 ({intent}): {e}")
                if inner_msg:
                    print(f"   -> 内层异常: {inner_msg}")

                err_str = str(e) + inner_msg
                if "task_status.started" in err_str:
                    print(f"[MCP] 💡 诊断：SSE 服务器握手阶段断开")
                    print(f"[MCP]    当前端点: {url}")
                    print(f"[MCP]    当前 Key: {api_key[:10]}...{api_key[-4:]}")
                return None
        return None

    @staticmethod
    def run_async(coro):
        """在独立线程中运行异步协程，避免与 Uvicorn 事件循环冲突。"""
        def _run():
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_run)
            return future.result(timeout=120)
