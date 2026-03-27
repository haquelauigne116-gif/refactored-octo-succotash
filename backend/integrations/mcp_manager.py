import asyncio
import shutil
import httpx  # type: ignore[import]
from contextlib import AsyncExitStack
from mcp.client.session import ClientSession  # type: ignore[import]
from mcp.client.sse import sse_client  # type: ignore[import]
from mcp.client.streamable_http import streamable_http_client  # type: ignore[import]
from mcp.client.stdio import stdio_client  # type: ignore[import]
from mcp import StdioServerParameters  # type: ignore[import]
from backend.config import APP_SETTINGS  # type: ignore[import]

# MCP 服务端点配置
# type="remote"  → 百炼远程 SSE / Streamable HTTP（需要 bailian_api_key）
# type="stdio"   → 本地 stdio 子进程（免费，无需 API Key）
# type="builtin" → 内置实现（无需外部服务）
MCP_ENDPOINTS: dict = {
    "Z_IMAGE": {"type": "remote", "url": "https://dashscope.aliyuncs.com/api/v1/mcps/zimage/mcp"},
    "AMAP": {"type": "remote", "url": "https://dashscope.aliyuncs.com/api/v1/mcps/amap-maps/mcp"},
    "WEB_SEARCH": {"type": "remote", "url": "https://dashscope.aliyuncs.com/api/v1/mcps/zhipu-websearch/sse"},
    "TTS": {"type": "remote", "url": "https://dashscope.aliyuncs.com/api/v1/mcps/QwenTextToSpeech/mcp"},
    "JIMENG": {"type": "builtin"},
}

class MCPManager:
    def __init__(self):
        # 缓存单例的事件池以防重复请求？目前的场景下可以用完即毁，或实现简易缓存
        pass

    def _clean_json_schema(self, schema: dict) -> dict:
        """递归清理和规范化 Bailian 返回的不标准 JSON Schema 类型。
        将其转换为 OpenAI 严格要求的标准 JSON Schema (例如 bool -> boolean, int -> integer)
        """
        if not isinstance(schema, dict):
            return schema
            
        type_mapping = {
            "bool": "boolean",
            "int": "integer",
            "str": "string",
            "dict": "object",
            "list": "array",
            "float": "number"
        }
        
        cleaned = {}  # type: ignore
        for k, v in schema.items():
            if k == "type" and isinstance(v, str) and v in type_mapping:
                cleaned[k] = type_mapping[v]  # type: ignore
            elif isinstance(v, dict):
                cleaned[k] = self._clean_json_schema(v)  # type: ignore
            elif isinstance(v, list):
                cleaned[k] = [self._clean_json_schema(item) if isinstance(item, dict) else item for item in v]  # type: ignore
            else:
                cleaned[k] = v  # type: ignore
        return cleaned

    async def _preflight_check(self, url: str, api_key: str) -> tuple[bool, str]:
        """在尝试 SSE/Streamable HTTP 连接前，先做一次 HTTP 预检请求。
        返回 (通过, 诊断信息)。
        注意: SSE 端点的 GET 请求会持续返回流数据导致超时，因此用 HEAD。
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as check_client:
                method = "HEAD" if url.endswith("/sse") else "GET"
                resp = await check_client.request(
                    method,
                    url,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                if resp.status_code == 401:
                    return False, f"API Key 认证失败 (HTTP 401)，请检查 bailian_api_key 是否正确"
                if resp.status_code == 403:
                    return False, f"API Key 无权限访问此服务 (HTTP 403)，请确认已开通对应 MCP 服务"
                if resp.status_code == 404:
                    return False, f"MCP 服务端点不存在 (HTTP 404)，URL 可能已变更: {url}"
                if resp.status_code >= 500:
                    return False, f"MCP 服务端内部错误 (HTTP {resp.status_code})，请稍后重试"
                # 200 或其他 2xx/3xx 都视为可连接
                return True, f"预检通过 (HTTP {resp.status_code})"
        except httpx.ConnectError:
            return False, f"无法连接到 MCP 服务器: {url}，请检查网络"
        except httpx.TimeoutException:
            return False, f"连接 MCP 服务器超时 (15s): {url}"
        except Exception as e:
            # 预检异常不阻止后续尝试，只警告
            print(f"[MCP] 预检请求异常 (非阻断): {e}")
            return True, f"预检异常但不阻断: {e}"

    async def _connect_and_run(self, intent: str, callback):
        """统一连接逻辑：根据端点类型选择 stdio 或远程连接，然后执行 callback(session)。"""
        endpoint = MCP_ENDPOINTS.get(intent)
        if not endpoint:
            return None

        ep_type = endpoint.get("type", "remote")

        if ep_type == "stdio":
            return await self._run_stdio(intent, endpoint, callback)
        else:
            return await self._run_remote(intent, endpoint, callback)

    async def _run_stdio(self, intent: str, endpoint: dict, callback):
        """通过 stdio 子进程连接本地 MCP 服务器（如 DuckDuckGo）。"""
        command = endpoint["command"]
        env = endpoint.get("env", {})

        # 解析命令的完整路径（Windows 上 pip --user 安装的脚本可能不在 PATH 中）
        resolved = shutil.which(command)
        if not resolved:
            # 尝试 Python 用户脚本目录
            import os, site
            user_scripts = os.path.join(site.getusersitepackages(), "..", "Scripts")
            user_scripts = os.path.abspath(user_scripts)
            candidate = os.path.join(user_scripts, command + ".exe")
            if os.path.exists(candidate):
                resolved = candidate
                print(f"[MCP] 在用户脚本目录找到命令: {resolved}")
            else:
                print(f"[MCP] ❌ 未找到命令 '{command}'，请确认已安装: pip install {command}")
                return None

        server_params = StdioServerParameters(
            command=resolved,
            env=env,
        )

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

    async def _run_remote(self, intent: str, endpoint: dict, callback):
        """通过远程 SSE / Streamable HTTP 连接百炼 MCP 服务器。"""
        url = endpoint["url"]
        api_key = APP_SETTINGS.get("bailian_api_key", "").strip()
        if not api_key:
            print(f"[MCP] 未配置 bailian_api_key，无法加载 {intent} 对应的工具")
            return None

        # 预检
        ok, diag = await self._preflight_check(url, api_key)
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
                        http_client = httpx.AsyncClient(headers={"Authorization": f"Bearer {api_key}"}, timeout=300.0)
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
                    print(f"[MCP] SSE 连接失败 (第 {attempt+1} 次)，{wait_sec}s 后重试... 原因: {e}{inner_msg}")
                    await asyncio.sleep(wait_sec)
                    continue

                print(f"[MCP] 获取工具列表失败 ({intent}): {e}")
                if inner_msg:
                    print(f"   -> 内层异常: {inner_msg}")

                err_str = str(e) + inner_msg
                if "task_status.started" in err_str:
                    print(f"[MCP] 💡 诊断：SSE 服务器在握手阶段就断开了连接。")
                    print(f"[MCP]    可能原因: 1) API Key 无效  2) 该 MCP 服务暂时不可用  3) 服务端点 URL 已变更")
                    print(f"[MCP]    当前端点: {url}")
                    print(f"[MCP]    当前 Key: {api_key[:10]}...{api_key[-4:]}")
                return None
        return None

    async def _fetch_tools_async(self, intent: str) -> list[dict]:
        endpoint = MCP_ENDPOINTS.get(intent)
        if not endpoint:
            return []

        # 内置工具：直接返回预定义的工具列表
        if endpoint.get("type") == "builtin":
            return self._get_builtin_tools(intent)

        async def _list(session: ClientSession) -> list[dict]:
            tools_response = await session.list_tools()
            openai_tools = []
            for t in tools_response.tools:
                cleaned_params = self._clean_json_schema(t.inputSchema) if t.inputSchema else {"type": "object", "properties": {}}
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": cleaned_params
                    }
                })
            print(f"[MCP] 成功获取 {intent} 提供的工具: {[t['function']['name'] for t in openai_tools]}")  # type: ignore[index]
            return openai_tools

        result = await self._connect_and_run(intent, _list)
        return result if result is not None else []

    async def _execute_tool_async(self, intent: str, tool_name: str, args: dict, session_id: str = "", session_dir: str = "") -> str:
        endpoint = MCP_ENDPOINTS.get(intent)
        if not endpoint:
            return "MCP 端点不存在或不支持此意图"

        # 内置工具：直接执行，也需要走媒体处理
        if endpoint.get("type") == "builtin":
            out_text = await self._execute_builtin(intent, tool_name, args)
            if session_id and session_dir and ("http://" in out_text or "https://" in out_text):
                out_text = await self._process_media_downloads(out_text, session_id, session_dir)
            return out_text

        async def _call(session: ClientSession) -> str:
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
                out_text = await self._process_media_downloads(out_text, session_id, session_dir)

            return out_text

        result = await self._connect_and_run(intent, _call)
        return result if result is not None else f"Error: 工具执行失败 ({tool_name})"

    def get_tools_for_intent(self, intent: str) -> list[dict]:
        """同步获取适用于特定意图的 Tools"""
        if not APP_SETTINGS.get("enable_mcp_for_chat", False):
            return []
        if not intent or intent == "NONE":
            return []
        
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
        return loop.run_until_complete(self._fetch_tools_async(intent))

    def get_all_builtin_tools(self) -> list[dict]:
        """同步获取所有有内置实现的 MCP 工具（不需要远程连接即可加载定义）。
        包括 type='builtin' 的端点以及虽然有远程端点但也有内置实现的工具。"""
        if not APP_SETTINGS.get("enable_mcp_for_chat", False):
            return []
        all_tools = []
        # 尝试从所有 intent 获取内置工具定义
        for intent_key in MCP_ENDPOINTS:
            try:
                builtin = self._get_builtin_tools(intent_key)
                if builtin:
                    all_tools.extend(builtin)
            except Exception:
                pass  # 该 intent 没有内置实现，跳过
        return all_tools

    def get_all_tools_for_loop(self, detected_intent: str) -> list[dict]:
        """获取循环所需的所有工具：核心内置工具 + 检测到的远程意图工具。
        始终加载 JIMENG 和 WEB_SEARCH 供 AI 自主选用。"""
        if not APP_SETTINGS.get("enable_mcp_for_chat", False):
            return []
        tools = []
        loaded_names: set[str] = set()
        # 1. 始终加载核心意图的工具（JIMENG + WEB_SEARCH）
        for core_intent in ["JIMENG", "WEB_SEARCH"]:
            try:
                intent_tools = self.get_tools_for_intent(core_intent)
                for t in intent_tools:
                    fname = t["function"]["name"]
                    if fname not in loaded_names:
                        tools.append(t)
                        loaded_names.add(fname)
            except Exception:
                pass
        # 2. 如果检测到的意图不在核心集合中，也加载它
        if detected_intent and detected_intent != "NONE" and detected_intent not in ("JIMENG", "WEB_SEARCH"):
            try:
                remote_tools = self.get_tools_for_intent(detected_intent)
                for t in remote_tools:
                    fname = t["function"]["name"]
                    if fname not in loaded_names:
                        tools.append(t)
                        loaded_names.add(fname)
            except Exception:
                pass
        return tools

    def execute_tool(self, intent: str, tool_name: str, args: dict, session_id: str = "", session_dir: str = "") -> str:
        """同步执行特定意图下的工具调用"""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
        return loop.run_until_complete(self._execute_tool_async(intent, tool_name, args, session_id, session_dir))

    # ====== 内置工具实现（无需外部 MCP 服务） ======

    def _get_builtin_tools(self, intent: str) -> list[dict]:
        """返回内置工具的 OpenAI function 定义。"""
        if intent == "WEB_SEARCH":
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "搜索互联网获取最新信息、新闻、实时数据等。使用 Bing 搜索引擎。",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "搜索关键词"
                                },
                                "max_results": {
                                    "type": "integer",
                                    "description": "返回的最大结果数量，默认 8",
                                }
                            },
                            "required": ["query"]
                        }
                    }
                }
            ]
            print(f"[MCP] 内置工具已就绪 (WEB_SEARCH): ['web_search']")
            return tools

        if intent == "JIMENG":
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "jimeng_image_generation",
                        "description": "使用火山引擎即梦 AI 生成图片。支持文生图（根据文字描述生成图片）和图生图（基于参考图片编辑生成）。",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "prompt": {
                                    "type": "string",
                                    "description": "图片描述提示词，中英文均可。建议120字以内。"
                                },
                                "model": {
                                    "type": "string",
                                    "description": "模型版本。可选: v30(3.0文生图), i2i_v30(3.0图生图), v40(4.0统一版)。默认v30。",
                                },
                                "image_urls": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "参考图片URL列表，用于图生图场景。"
                                },
                                "width": {
                                    "type": "integer",
                                    "description": "生成图片宽度，需与height同时传入。推荐1328。"
                                },
                                "height": {
                                    "type": "integer",
                                    "description": "生成图片高度，需与width同时传入。推荐1328。"
                                },
                                "scale": {
                                    "type": "number",
                                    "description": "文本影响程度(0-1)，值越大文本影响越大、图片影响越小。仅图生图有效。默认0.5。"
                                }
                            },
                            "required": ["prompt"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "jimeng_video_generation",
                        "description": "使用火山引擎即梦 AI 生成视频。支持文生视频（根据文字描述生成视频）和图生视频（基于图片生成视频）。注意：视频生成耗时较长(1-5分钟)。",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "prompt": {
                                    "type": "string",
                                    "description": "视频描述提示词，中英文均可。建议400字以内。"
                                },
                                "model": {
                                    "type": "string",
                                    "description": "模型版本。可选: v30(3.0文/图生视频), pro(3.0Pro)。默认v30。",
                                },
                                "image_urls": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "参考图片URL。1张=首帧图生视频，2张=首尾帧图生视频。"
                                },
                                "frames": {
                                    "type": "integer",
                                    "description": "视频帧数。121=5秒，241=10秒。默认121。"
                                },
                                "aspect_ratio": {
                                    "type": "string",
                                    "description": "视频宽高比。可选: 16:9, 4:3, 1:1, 3:4, 9:16, 21:9。默认16:9。"
                                }
                            },
                            "required": ["prompt"]
                        }
                    }
                }
            ]
            print(f"[MCP] 内置工具已就绪 (JIMENG): ['jimeng_image_generation', 'jimeng_video_generation']")
            return tools

        return []

    async def _execute_builtin(self, intent: str, tool_name: str, args: dict) -> str:
        """执行内置工具。"""
        if intent == "WEB_SEARCH" and tool_name == "web_search":
            query = args.get("query", "")
            max_results = args.get("max_results", 8)
            return await self._bing_search(query, max_results)
        if intent == "JIMENG":
            return await self._execute_jimeng(tool_name, args)
        return f"Error: 未知的内置工具 {tool_name}"

    async def _bing_search(self, query: str, max_results: int = 8) -> str:
        """通过抓取 Bing 搜索页面获取搜索结果（免费、无需 API Key、国内可用）。"""
        import re
        from html import unescape

        print(f"[MCP] 执行 Bing 搜索: '{query}' (max_results={max_results})")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(
                    "https://www.bing.com/search",
                    params={"q": query, "count": str(max_results)},
                    headers=headers,
                )
                if resp.status_code != 200:
                    return f"搜索请求失败 (HTTP {resp.status_code})"

                html = resp.text

                results = []
                algo_blocks = re.findall(r'<li class="b_algo"[^>]*>(.*?)</li>', html, re.DOTALL)

                for block in algo_blocks[:max_results]:
                    title_match = re.search(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
                    if not title_match:
                        continue

                    url = title_match.group(1)
                    title = re.sub(r'<[^>]+>', '', title_match.group(2)).strip()
                    title = unescape(title)

                    snippet = ""
                    snippet_match = re.search(r'<p[^>]*class="[^"]*b_lineclamp[^"]*"[^>]*>(.*?)</p>', block, re.DOTALL)
                    if not snippet_match:
                        snippet_match = re.search(r'<div class="b_caption"[^>]*>.*?<p[^>]*>(.*?)</p>', block, re.DOTALL)
                    if not snippet_match:
                        snippet_match = re.search(r'<p>(.*?)</p>', block, re.DOTALL)

                    if snippet_match:
                        snippet = re.sub(r'<[^>]+>', '', snippet_match.group(1)).strip()
                        snippet = unescape(snippet)

                    if title and url and not url.startswith("javascript:"):
                        results.append({"title": title, "url": url, "snippet": snippet})

                if not results:
                    link_blocks = re.findall(r'<h2[^>]*>.*?<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>.*?</h2>', html, re.DOTALL)
                    for url, title_html in link_blocks[:max_results]:
                        title = re.sub(r'<[^>]+>', '', title_html).strip()
                        title = unescape(title)
                        if title:
                            results.append({"title": title, "url": url, "snippet": ""})

                if not results:
                    return "未找到相关搜索结果。请尝试修改关键词后重试。"

                output_parts = [f"Bing 搜索结果 ({len(results)} 条):\n"]
                for i, r in enumerate(results, 1):
                    output_parts.append(f"{i}. {r['title']}")
                    output_parts.append(f"   链接: {r['url']}")
                    if r['snippet']:
                        output_parts.append(f"   摘要: {r['snippet']}")
                    output_parts.append("")

                result_text = "\n".join(output_parts)
                print(f"[MCP] Bing 搜索成功: 找到 {len(results)} 条结果")
                return result_text

        except httpx.TimeoutException:
            return "搜索超时，请稍后重试。"
        except Exception as e:
            print(f"[MCP] Bing 搜索异常: {e}")
            return f"搜索失败: {str(e)}"

    async def _execute_jimeng(self, tool_name: str, args: dict) -> str:
        """执行即梦 AI 图片/视频生成工具。"""
        import asyncio
        from backend.integrations.volcengine_service import jimeng_service  # type: ignore[import]

        prompt = args.get("prompt", "")
        if not prompt:
            return "Error: 缺少必要参数 prompt"

        if tool_name == "jimeng_image_generation":
            # 根据 model 参数选择 req_key
            model = args.get("model", "v30")
            req_key_map = {
                "v30": "jimeng_t2i_v30",
                "i2i_v30": "jimeng_i2i_v30",
                "v40": "jimeng_t2i_v40",
            }
            req_key = req_key_map.get(model, "jimeng_t2i_v30")

            # 如果提供了图片但没指定 model，自动选择图生图
            image_urls = args.get("image_urls")
            if image_urls and model == "v30":
                req_key = "jimeng_i2i_v30"

            kwargs = {"prompt": prompt, "req_key": req_key}
            if image_urls:
                kwargs["image_urls"] = image_urls
            if args.get("width") and args.get("height"):
                kwargs["width"] = args["width"]
                kwargs["height"] = args["height"]
            if args.get("scale") is not None:
                kwargs["scale"] = args["scale"]

            print(f"[Jimeng] 执行图片生成: req_key={req_key}, prompt={prompt[:50]}")
            # 在线程池中执行同步的 SDK 调用（避免阻塞事件循环）
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: jimeng_service.generate_image(**kwargs))

            if result["status"] == "ok":
                urls = result.get("image_urls", [])
                if urls:
                    url_list = "\n".join(urls)
                    return f"✅ 即梦图片生成成功！\n生成了 {len(urls)} 张图片:\n{url_list}"
                else:
                    return "✅ 即梦图片生成完成，但未返回图片URL。"
            else:
                return f"❌ 即梦图片生成失败: {result.get('message', '未知错误')}"

        elif tool_name == "jimeng_video_generation":
            model = args.get("model", "v30")
            image_urls = args.get("image_urls")

            # 根据 model 和图片数量选择 req_key
            if model == "pro":
                req_key = "jimeng_ti2v_v30_pro"
            elif image_urls:
                if len(image_urls) >= 2:
                    req_key = "jimeng_i2v_first_tail_v30_1080"
                else:
                    req_key = "jimeng_i2v_first_v30_1080"
            else:
                req_key = "jimeng_t2v_v30_1080p"

            kwargs = {
                "prompt": prompt,
                "req_key": req_key,
                "frames": args.get("frames", 121),
                "aspect_ratio": args.get("aspect_ratio", "16:9"),
            }
            if image_urls:
                kwargs["image_urls"] = image_urls

            print(f"[Jimeng] 执行视频生成: req_key={req_key}, prompt={prompt[:50]}")
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: jimeng_service.generate_video(**kwargs))

            if result["status"] == "ok":
                video_url = result.get("video_url", "")
                if video_url:
                    return f"✅ 即梦视频生成成功！\n视频链接（1小时有效）: {video_url}"
                else:
                    return "✅ 即梦视频生成完成，但未返回视频URL。"
            else:
                return f"❌ 即梦视频生成失败: {result.get('message', '未知错误')}"

        return f"Error: 未知的即梦工具 {tool_name}"


    async def _process_media_downloads(self, text: str, session_id: str, session_dir: str) -> str:
        """统一处理工具返回中的媒体 URL：下载图片/视频到本地 assets 并生成内联 HTML。"""
        import re
        import os
        import uuid as _uuid
        try:
            from PIL import Image  # type: ignore[import]
            has_pil = True
        except ImportError:
            has_pil = False

        urls = re.findall(r'https?://[^\s"\'\\]+', text)
        if not urls:
            return text

        assets_dir = os.path.join(session_dir, "assets")
        os.makedirs(assets_dir, exist_ok=True)
        replaced_text = text
        has_image = False
        has_video = False

        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
            for url in urls:
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        continue

                    content_type = resp.headers.get("Content-Type", "")
                    media_id = str(_uuid.uuid4())[:8]  # type: ignore

                    # ---- 图片处理 ----
                    if content_type.startswith("image/"):
                        ext = content_type.split("/")[-1].split(";")[0]
                        if ext not in ["jpeg", "png", "gif", "webp"]:
                            ext = "png"

                        filename = f"img_{media_id}.{ext}"
                        thumb_name = f"img_{media_id}_thumb.{ext}"
                        full_path = os.path.join(assets_dir, filename)
                        thumb_path = os.path.join(assets_dir, thumb_name)

                        with open(full_path, "wb") as f:
                            f.write(resp.content)

                        if has_pil:
                            try:
                                img = Image.open(full_path)
                                img.thumbnail((300, 300))
                                if img.mode != "RGB" and ext == "jpeg":
                                    img = img.convert("RGB")
                                img.save(thumb_path)
                            except Exception as e:
                                print(f"[MCP] 缩略图生成失败: {e}")
                                thumb_name = filename
                        else:
                            thumb_name = filename

                        render_html = f'<a href="/assets/{session_id}/assets/{filename}" target="_blank"><img src="/assets/{session_id}/assets/{thumb_name}" alt="AI 生成图片" /></a>'
                        replaced_text = replaced_text.replace(url, render_html)
                        has_image = True
                        print(f"[MCP] 图片已下载: {filename}")

                    # ---- 视频处理 ----
                    elif content_type.startswith("video/") or any(ext in url.lower() for ext in [".mp4", ".webm", ".mov"]):
                        ext = "mp4"
                        if content_type.startswith("video/"):
                            ext = content_type.split("/")[-1].split(";")[0]
                            if ext not in ["mp4", "webm", "mov"]:
                                ext = "mp4"

                        filename = f"vid_{media_id}.{ext}"
                        full_path = os.path.join(assets_dir, filename)

                        with open(full_path, "wb") as f:
                            f.write(resp.content)

                        render_html = f'<video controls src="/assets/{session_id}/assets/{filename}" style="max-width:100%;border-radius:12px;"></video>'
                        replaced_text = replaced_text.replace(url, render_html)
                        has_video = True
                        print(f"[MCP] 视频已下载: {filename}")

                except Exception as e:
                    print(f"[MCP] 媒体下载失败: {url}, Error: {e}")

        # 追加指令让 LLM 正确输出 HTML
        if has_image or has_video:
            media_type = "图片" if has_image else "视频"
            replaced_text += f"\n【重要指令】拦截成功！{media_type}已下载到本地。请简要描述生成内容即可，不要输出任何链接或HTML代码。"

        return replaced_text

    # 保持旧方法名的向后兼容
    async def _process_image_downloads(self, text: str, session_id: str, session_dir: str) -> str:
        return await self._process_media_downloads(text, session_id, session_dir)

mcp_mgr = MCPManager()
