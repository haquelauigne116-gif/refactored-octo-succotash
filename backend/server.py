"""
server.py — FastAPI 路由入口 (精简版，业务逻辑已拆分到各模块)
"""
from contextlib import asynccontextmanager
from typing import Optional

import asyncio
import os
import uuid as _uuid

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, Query  # type: ignore[import]
from fastapi.middleware.cors import CORSMiddleware  # type: ignore[import]
from fastapi.staticfiles import StaticFiles  # type: ignore[import]
from fastapi.responses import FileResponse, StreamingResponse  # type: ignore[import]
from pydantic import BaseModel  # type: ignore[import]

from backend.config import (  # type: ignore[import]
    SYSTEM_PROMPT, INDEX_HTML, FRONTEND_DIR,
    API_PROVIDERS, APP_SETTINGS, SESSION_DIR,
    get_client, get_model_caps, load_settings, save_settings, save_providers_config,
    MEMORY_FILE,
    load_notification_config, save_notification_config,
)
from backend.session_manager import SessionManager  # type: ignore[import]
from backend.rag_engine import RAGEngine  # type: ignore[import]
from backend.task_scheduler import TaskScheduler  # type: ignore[import]
from backend.notification_manager import (  # type: ignore[import]
    NotificationManager, WebSocketChannel, DingTalkChannel,
)
from backend.dingtalk_handler import DingTalkChatHandler  # type: ignore[import]
from backend.memory_worker import DailyMemoryWorker  # type: ignore[import]
from backend.minio_manager import MinIOManager  # type: ignore[import]
from backend.mcp_manager import mcp_mgr  # type: ignore[import]
from backend.schedule_manager import ScheduleManager  # type: ignore[import]

# ====== 初始化核心组件 ======
session_mgr = SessionManager()
rag = RAGEngine()
task_scheduler = TaskScheduler()
minio_mgr = MinIOManager()
schedule_mgr = ScheduleManager()

# 当前对话模型状态
current_provider_id = "deepseek"
current_model = "deepseek-chat"

if API_PROVIDERS:
    if current_provider_id not in API_PROVIDERS:
        current_provider_id = list(API_PROVIDERS.keys())[0]
        current_model = API_PROVIDERS[current_provider_id]["models"][0]["id"]

client = get_client(current_provider_id)

# ====== WebSocket 连接管理 ======
ws_clients: set[WebSocket] = set()
_event_loop = None  # 主事件循环引用
notification_mgr: NotificationManager | None = None  # 多通道通知管理器

async def broadcast(data: dict):
    """向所有连接的 WebSocket 客户端广播消息"""
    dead: list[WebSocket] = []
    for client in list(ws_clients):
        try:
            await client.send_json(data)
        except Exception:
            dead.append(client)
    for c in dead:
        ws_clients.discard(c)

def broadcast_sync(data: dict):
    """同步回调桥接：从 APScheduler 线程安全地推送到 async WebSocket"""
    if _event_loop and _event_loop.is_running():
        asyncio.run_coroutine_threadsafe(broadcast(data), _event_loop)

# ====== FastAPI 应用 ======

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时开启调度器和通知通道，关闭时停止"""
    global _event_loop, notification_mgr
    _event_loop = asyncio.get_running_loop()

    # 初始化多通道通知管理器
    notif_config = load_notification_config()
    channels_cfg = notif_config.get("channels", {})

    notification_mgr = NotificationManager()

    # WebSocket 通道
    ws_channel = WebSocketChannel()
    ws_cfg = channels_cfg.get("websocket", {})
    ws_channel.enabled = ws_cfg.get("enabled", True)
    ws_channel.set_broadcast_fn(broadcast_sync)
    notification_mgr.add_channel(ws_channel)

    # 钉钉通道 + 聊天处理器
    dt_cfg = channels_cfg.get("dingtalk", {})
    dt_channel = DingTalkChannel(
        app_key=dt_cfg.get("app_key", ""),
        app_secret=dt_cfg.get("app_secret", ""),
        agent_id=dt_cfg.get("agent_id", ""),
        robot_code=dt_cfg.get("robot_code", ""),
        open_conversation_id=dt_cfg.get("open_conversation_id", ""),
        user_ids=dt_cfg.get("user_ids", []),
        msg_type=dt_cfg.get("msg_type", "single"),
        enabled=dt_cfg.get("enabled", False),
    )
    notification_mgr.add_channel(dt_channel)

    # 创建钉钉聊天处理器并注入
    dingtalk_chat_handler = DingTalkChatHandler(rag=rag, minio_mgr=minio_mgr)
    dingtalk_chat_handler.set_task_scheduler(task_scheduler)

    # 启动所有通道（传入聊天处理器）
    notification_mgr.start(chat_handler=dingtalk_chat_handler)

    # 注入到任务调度器
    task_scheduler.notification_manager = notification_mgr
    task_scheduler.start()

    # 注入日程管理器
    schedule_mgr.task_scheduler = task_scheduler
    schedule_mgr.notification_manager = notification_mgr

    from apscheduler.triggers.cron import CronTrigger  # type: ignore[import]
    from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import]

    # 注册每日记忆整理 (凌晨 2:00)
    memory_worker = DailyMemoryWorker()
    task_scheduler.register_system_job(
        job_id="daily_memory_extraction",
        func=memory_worker.run,
        trigger=CronTrigger(hour=2, minute=0),
    )
    print("[System] 每日记忆整理已注册 (凌晨 2:00)")

    # 注册日程提醒轮询 (每分钟扫描一次, 仅占 1 个 job)
    task_scheduler.register_system_job(
        job_id="schedule_reminder_poll",
        func=schedule_mgr.check_and_fire_reminders,
        trigger=IntervalTrigger(minutes=1),
    )
    print("[System] 日程提醒轮询已注册 (每分钟)")

    # 注册每日日程简报 (早上 8:00, 仅占 1 个 job)
    task_scheduler.register_system_job(
        job_id="daily_schedule_briefing",
        func=schedule_mgr.send_daily_briefing,
        trigger=CronTrigger(hour=8, minute=0),
    )
    print("[System] 每日日程简报已注册 (早上 8:00)")

    yield

    task_scheduler.shutdown()
    notification_mgr.shutdown()
    _event_loop = None

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ====== 页面路由 ======

@app.get("/")
def serve_index():
    return FileResponse(INDEX_HTML)

# ====== 模型提供商路由 ======

@app.get("/providers")
def get_providers():
    return {
        "current_provider": current_provider_id,
        "current_model": current_model,
        "providers": [
            {"id": k, "name": v["name"], "models": v["models"]}
            for k, v in API_PROVIDERS.items()
        ],
    }

class ProviderRequest(BaseModel):
    provider_id: str
    model_id: str

@app.post("/switch_provider")
def switch_provider(request: ProviderRequest):
    global current_provider_id, client, current_model
    if request.provider_id not in API_PROVIDERS:
        return {"status": "error", "message": "未知的提供商"}
    models = [m["id"] for m in API_PROVIDERS[request.provider_id]["models"]]
    if request.model_id not in models:
        return {"status": "error", "message": "未知的模型"}
    current_provider_id = request.provider_id
    current_model = request.model_id
    client = get_client(current_provider_id)
    return {"status": "ok", "provider": current_provider_id, "model": current_model}

# ====== 设置路由 ======

@app.get("/api/settings")
def get_system_settings():
    safe_providers = {}
    for pid, pdata in API_PROVIDERS.items():
        key = pdata.get("api_key", "")
        masked = f"{key[:6]}...{key[-4:]}" if len(key) > 15 else "未配置或过短"
        safe_providers[pid] = {"name": pdata["name"], "api_key_masked": masked}
    return {"providers_status": safe_providers, "settings": APP_SETTINGS}

class SettingsUpdateRequest(BaseModel):
    api_keys: dict
    summary_provider: str
    summary_model: str
    judge_provider: str
    judge_model: str
    bailian_api_key: str = ""
    enable_mcp_for_chat: bool = False

@app.post("/api/settings")
def update_system_settings(req: SettingsUpdateRequest):
    global API_PROVIDERS
    # 更新 API Keys
    changed = False
    for pid, new_key in req.api_keys.items():
        if pid in API_PROVIDERS and new_key and len(new_key.strip()) > 10:
            API_PROVIDERS[pid]["api_key"] = new_key.strip()
            changed = True
    if changed:
        save_providers_config(API_PROVIDERS)

    # 更新系统设置
    APP_SETTINGS["summary_provider"] = req.summary_provider
    APP_SETTINGS["summary_model"] = req.summary_model
    APP_SETTINGS["judge_provider"] = req.judge_provider
    APP_SETTINGS["judge_model"] = req.judge_model
    APP_SETTINGS["bailian_api_key"] = req.bailian_api_key
    APP_SETTINGS["enable_mcp_for_chat"] = req.enable_mcp_for_chat
    save_settings(APP_SETTINGS)
    return {"status": "ok"}

# ====== 会话路由 ======

@app.get("/sessions")
def list_sessions():
    return {"sessions": session_mgr.list_all()}

@app.post("/new_session")
def new_session():
    session_mgr.create_new()
    return {"status": "ok", "session_id": session_mgr.session_id}

class SwitchRequest(BaseModel):
    filename: str

@app.post("/switch_session")
def switch_session(request: SwitchRequest):
    result = session_mgr.switch_to(request.filename)
    if result is None:
        return {"status": "error", "message": "会话不存在"}
    return {"status": "ok", "messages": result["messages"], "events": result["events"]}

# ====== 聊天路由 ======

class ChatRequest(BaseModel):
    user_word: str = ""
    attachments: list[dict] | None = None  # [{data: base64, mime: str, name: str}]

# ====== 附件文本提取 ======

def _extract_doc_text(att: dict) -> str:
    """从 base64 编码的附件提取可读文本 (PDF / txt / doc / docx / xls / xlsx 等)"""
    import base64 as _b64
    import io as _io
    import re as _re

    mime = att.get("mime", "")
    name = att.get("name", "")
    data_b64 = att.get("data", "")
    if not data_b64:
        return ""

    try:
        raw = _b64.b64decode(data_b64)
    except Exception:
        return ""

    lower_name = name.lower()

    # PDF: 使用 PyPDF2 提取
    if mime == "application/pdf" or lower_name.endswith(".pdf"):
        try:
            from PyPDF2 import PdfReader  # type: ignore[import]
            reader = PdfReader(_io.BytesIO(raw))
            pages: list[str] = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    pages.append(text.strip())
            full_text = "\n\n".join(pages)
            return full_text[:5000]  # type: ignore[index]
        except ImportError:
            return "[系统提示: 需要安装 PyPDF2 以提取 PDF 内容: pip install PyPDF2]"
        except Exception as e:
            return f"[PDF 解析失败: {e}]"

    # Word .docx (新格式)
    if lower_name.endswith(".docx"):
        try:
            from docx import Document  # type: ignore[import]
            doc = Document(_io.BytesIO(raw))
            text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            return text[:5000]  # type: ignore[index]
        except ImportError:
            return "[系统提示: 需要安装 python-docx 以提取 Word 内容: pip install python-docx]"
        except Exception as e:
            return f"[Word 解析失败: {e}]"

    # Word .doc (旧二进制格式)
    if lower_name.endswith(".doc") or mime == "application/msword":
        try:
            # 方法 1: 尝试 olefile 提取 Word 文档流
            try:
                import olefile  # type: ignore[import]
                ole = olefile.OleFileIO(_io.BytesIO(raw))
                if ole.exists("WordDocument"):
                    # 提取主文档流中的文本 (简易方式)
                    stream = ole.openstream("WordDocument").read()
                    # 去除 null 字节, 提取可读文本
                    text = stream.replace(b'\x00', b'').decode('utf-8', errors='ignore')
                    text = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)
                    # 过滤掉太短的片段 (二进制噪声)
                    parts = [p.strip() for p in text.split('\r') if len(p.strip()) > 2]
                    result = "\n".join(parts)
                    if len(result) > 50:
                        return result[:5000]  # type: ignore[index]
                ole.close()
            except ImportError:
                pass

            # 方法 2: 直接从原始二进制中提取文本 (通用回退)
            text = raw.replace(b'\x00', b'').decode('utf-8', errors='ignore')
            text = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)
            # 提取较长的连续文本段落
            parts = [p.strip() for p in _re.split(r'[\r\n]+', text) if len(p.strip()) > 3]
            result = "\n".join(parts)
            if len(result) > 50:
                return result[:5000]  # type: ignore[index]
            return "[.doc 文件文本提取量不足, 建议转换为 .docx 格式后重试]"
        except Exception as e:
            return f"[.doc 解析失败: {e}，建议转换为 .docx 格式]"

    # Excel .xlsx
    if lower_name.endswith(".xlsx"):
        try:
            from openpyxl import load_workbook  # type: ignore[import]
            wb = load_workbook(_io.BytesIO(raw), read_only=True, data_only=True)
            rows_text: list[str] = []
            for ws in wb.worksheets:
                rows_text.append(f"=== 工作表: {ws.title} ===")
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c) if c is not None else "" for c in row]
                    if any(cells):
                        rows_text.append("\t".join(cells))
            wb.close()
            result = "\n".join(rows_text)
            return result[:5000]  # type: ignore[index]
        except ImportError:
            return "[系统提示: 需要安装 openpyxl 以提取 Excel 内容: pip install openpyxl]"
        except Exception as e:
            return f"[Excel 解析失败: {e}]"

    # Excel .xls (旧格式)
    if lower_name.endswith(".xls"):
        try:
            import xlrd  # type: ignore[import]
            book = xlrd.open_workbook(file_contents=raw)
            rows_text_xls: list[str] = []
            for sheet in book.sheets():
                rows_text_xls.append(f"=== 工作表: {sheet.name} ===")
                for rx in range(sheet.nrows):
                    cells = [str(sheet.cell_value(rx, cx)) for cx in range(sheet.ncols)]
                    if any(cells):
                        rows_text_xls.append("\t".join(cells))
            result = "\n".join(rows_text_xls)
            return result[:5000]  # type: ignore[index]
        except ImportError:
            return "[系统提示: 需要安装 xlrd 以提取 .xls 内容: pip install xlrd]"
        except Exception as e:
            return f"[.xls 解析失败: {e}]"

    # 纯文本类：txt, md, csv, json 等
    text_types = ("text/", "application/json", "application/xml", "application/csv")
    text_exts = (".txt", ".md", ".csv", ".json", ".xml", ".log", ".yml", ".yaml", ".ini", ".cfg")
    if any(mime.startswith(t) for t in text_types) or any(lower_name.endswith(e) for e in text_exts):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = raw.decode("gbk")
            except Exception:
                return "[文件编码无法识别]"
        return text[:5000]  # type: ignore[index]

    return ""

@app.post("/chat")
def chat_with_ai(request: ChatRequest):
    """SSE 流式聊天端点"""
    user_text = request.user_word
    attachments = request.attachments or []

    # --- 分析附件类型 ---
    image_attachments = [a for a in attachments if a.get("mime", "").startswith("image/")]
    doc_attachments = [a for a in attachments if not a.get("mime", "").startswith("image/")]

    # --- 提取文档内容 (PDF/txt 等) ---
    doc_text_parts: list[str] = []
    for att in doc_attachments:
        print(f"[Chat] 尝试提取文档: name={att.get('name')}, mime={att.get('mime')}, data_len={len(att.get('data', ''))}")
        extracted = _extract_doc_text(att)
        if extracted:
            doc_text_parts.append(f"[文件: {att.get('name', '未知')}]\n{extracted}")
            print(f"[Chat] 文档提取成功: {att.get('name')}, 提取长度={len(extracted)}")
        else:
            print(f"[Chat] 文档提取为空: {att.get('name')}")

    # --- 统一意图分析 ---
    intent = rag.analyze_intent(session_mgr.messages, user_text)
    rag_context = rag.retrieve_context(intent["rag_query"])
    task_intent = intent["task_intent"]
    schedule_intent = intent.get("schedule_intent")
    file_search_query = intent.get("file_search_query")

    # --- 第一条消息时初始化文件 ---
    if session_mgr.is_first_message:
        session_mgr.initialize_file()

    # --- 写入用户消息 ---
    attach_label = ""
    if attachments:
        names = [a.get("name", "文件") for a in attachments]
        attach_label = f" [附件: {', '.join(names)}]"
    session_mgr.append_user_message(user_text + attach_label)
    user_msg_index = len(session_mgr.messages) - 1  # 记录用户消息位置, 用于附件事件定位

    # --- 检查模型视觉能力 ---
    model_caps = get_model_caps(current_provider_id, current_model)
    has_vision = "vision" in model_caps

    # --- 构建 AI 消息列表 ---
    ai_messages = [m for m in session_mgr.messages if m.get("role") in ("system", "user", "assistant")]

    # 图片 + vision 模型 → Vision 格式
    if image_attachments and has_vision:
        last_user_idx = None
        for i in range(len(ai_messages) - 1, -1, -1):
            if ai_messages[i]["role"] == "user":
                last_user_idx = i
                break
        if last_user_idx is not None:
            content_parts: list[dict] = []
            if user_text:
                content_parts.append({"type": "text", "text": user_text})
            for img in image_attachments:
                data_url = f"data:{img['mime']};base64,{img['data']}"
                content_parts.append({"type": "image_url", "image_url": {"url": data_url}})
            if doc_text_parts:
                content_parts.append({"type": "text", "text": "\n\n".join(doc_text_parts)})
            ai_messages[last_user_idx] = {"role": "user", "content": content_parts}  # type: ignore[index]
    elif image_attachments and not has_vision:
        ai_messages.insert(-1, {"role": "system", "content": "用户发送了图片，但当前模型不支持图片理解。请友好地提示用户切换到支持视觉能力的模型（如 qwen-vl-plus / qwen-vl-max）后重试。"})

    # 文档文本注入
    if doc_text_parts and not (image_attachments and has_vision):
        print(f"[Chat] 注入文档上下文: {len(doc_text_parts)} 个文件")
        ai_messages.insert(-1, {"role": "system", "content": "用户附带了以下文件内容，请结合文件内容回答用户的问题：\n\n" + "\n\n".join(doc_text_parts)})

    if rag_context:
        ai_messages.insert(-1, {"role": "system", "content": f"以下是从本地知识库检索到的参考资料，请结合这些信息回答用户的问题：{rag_context}"})

    # 文件搜索
    search_res_json = None
    if file_search_query:
        search_res = minio_mgr.ai_search(file_search_query)
        if search_res.get("status") == "ok" and search_res.get("files"):
            search_res_json = search_res
            context_msg = f"系统已经根据用户的查找意图【{file_search_query}】从网盘中找出了相关文件，并会以卡片形式附加在你的回复下方。匹配原因：{search_res.get('reason', '')}。\n请你仅用一两句话亲切地告知用户找到了文件（可以适当提及简短原因），**绝对不要**在你的回复中展示文件的名称或下载链接。"
            ai_messages.insert(-1, {"role": "system", "content": context_msg})
        else:
            ai_messages.insert(-1, {"role": "system", "content": f"系统试图在网盘中查找描述为\u201c{file_search_query}\u201d的文件，但未找到匹配项。请在回复中礼貌地告知用户没有找到。"})

    # --- SSE 流式生成器 ---
    import json as _json

    def _sse_generator():
        thinking_buf = ""
        reply_buf = ""

        mcp_intent = intent.get("mcp_intent", "NONE")
        mcp_tools = mcp_mgr.get_tools_for_intent(mcp_intent)
        current_messages = list(ai_messages)
        
        if mcp_tools:
            current_messages.insert(0, {
                "role": "system",
                "content": "【重要指令】你已连接外部 MCP 工具。当用户要求画图、搜索或执行任务时，你**必须严格优先调用对应的 Tool**（如 modelstudio_z_image_generation），**绝对禁止**凭空伪造图片链接！只有在你确切执行了工具后即可，后续展示由前端强制完成，你只需文字总结即可。"
            })

        executed_tools_ui = set()  # Track which tool UI cards have been emitted across ALL sequential tool loop rounds

        for _ in range(10):  # 允许更深的循环调用（支持用户要求连抽7张图等场景）
            skip_temp = "reasoning" in model_caps or "fixed_temp" in model_caps
            stream_kwargs = {
                "model": current_model,
                "messages": current_messages,
                "stream": True
            }
            if not skip_temp:
                stream_kwargs["temperature"] = 0.7
            if mcp_tools:
                stream_kwargs["tools"] = mcp_tools

            try:
                stream = client.chat.completions.create(**stream_kwargs)
            except Exception as e:
                session_mgr.pop_last_message()
                error_type = type(e).__name__
                yield f"event: error\ndata: {_json.dumps({'message': f'{error_type}: {str(e)}'}, ensure_ascii=False)}\n\n"
                return

            tool_calls_buffer = {}
            has_tool_call = False

            for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if not delta:
                    continue

                # 1. 缓冲 tool_calls
                if getattr(delta, "tool_calls", None):
                    has_tool_call = True
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_buffer:
                            tool_calls_buffer[idx] = {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": ""}}
                        if getattr(tc.function, "arguments", None):
                            # type: ignore
                            tool_calls_buffer[idx]["function"]["arguments"] += tc.function.arguments

                # 检测 reasoning_content（推理思考内容，兼容多家模型）
                reasoning = (
                    getattr(delta, "reasoning_content", None)
                    or getattr(delta, "thinking", None)
                    or getattr(delta, "thinking_content", None)
                    or ""
                )
                if reasoning:
                    thinking_buf += reasoning  # type: ignore[operator]
                    yield f"event: thinking\ndata: {_json.dumps({'text': reasoning}, ensure_ascii=False)}\n\n"

                # 正文内容
                content = delta.content or ""
                if content:
                    reply_buf += content  # type: ignore[operator]
                    yield f"event: delta\ndata: {_json.dumps({'text': content}, ensure_ascii=False)}\n\n"
            
            # --- 单轮流结束，检查是否执行了工具 ---
            if has_tool_call and tool_calls_buffer:
                # 记录 assistant 消息中的 tool_calls
                assistant_tc_msg = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": list(tool_calls_buffer.values())
                }
                current_messages.append(assistant_tc_msg)

                # 逐个执行工具
                for tc in tool_calls_buffer.values():
                    func_dict2 = tc["function"]
                    t_name = func_dict2["name"]  # type: ignore[index]
                    t_args_str = func_dict2["arguments"]  # type: ignore[index]
                    try:
                        args_dict = _json.loads(t_args_str)
                    except:
                        args_dict = {}
                    
                    friendly_name = {
                        "modelstudio_z_image_generation": "Z-Image 图像生成",
                        "amap_poi_search": "高德地图路线",
                        "zhipu_web_search": "智谱联网搜索",
                        "qwen_tts": "智能语音合成",
                        "web_search": "Bing 联网搜索",
                        "webSearchPro": "联网搜索 Pro",
                        "webSearchStd": "联网搜索",
                        "webSearchSogou": "搜狗搜索",
                        "webSearchQuark": "夸克搜索",
                        "bailian_web_search": "百炼联网搜索",
                        "jimeng_image_generation": "即梦 AI 图片生成",
                        "jimeng_video_generation": "即梦 AI 视频生成",
                    }.get(t_name, t_name.replace("_", " ").title())
                    
                    working_card = f'<div class="tool-call-card working"><div class="tool-spinner"></div>正在调用技能：{friendly_name}</div>'
                    success_card = f'<div class="tool-call-card success">✅ 成功执行技能：{friendly_name}</div>'

                    TOOLS_ROW_START = '<div class="tool-cards-row">'
                    TOOLS_ROW_END = '</div><!-- END_TOOLS -->'
                    
                    # Revert success to working if it's already generated previously
                    if friendly_name not in executed_tools_ui:
                        # 首次出现此工具 — 添加到容器
                        if TOOLS_ROW_START not in reply_buf:
                            # 创建容器 + 放入第一个卡片
                            reply_buf = f'\n{TOOLS_ROW_START}{working_card}{TOOLS_ROW_END}\n' + reply_buf
                        else:
                            # 追加到容器内
                            reply_buf = reply_buf.replace(TOOLS_ROW_END, f'{working_card}{TOOLS_ROW_END}')
                        executed_tools_ui.add(friendly_name)
                        yield f"event: replace_all\ndata: {_json.dumps({'text': reply_buf}, ensure_ascii=False)}\n\n"
                    else:
                        # Revert the success card to working card temporarily
                        if success_card in reply_buf:
                            reply_buf = reply_buf.replace(success_card, working_card)
                            yield f"event: replace_all\ndata: {_json.dumps({'text': reply_buf}, ensure_ascii=False)}\n\n"
                    
                    t_result = mcp_mgr.execute_tool(
                        intent=mcp_intent,
                        tool_name=t_name,
                        args=args_dict,
                        session_id=session_mgr.session_id,
                        session_dir=session_mgr.session_dir
                    )
                    
                    t_result_str = str(t_result)
                    
                    # Intercept the HTML from mcp_manager and force layout it without LLM involvement
                    import re
                    img_match = re.search(r'<a href="/assets/[^>]+><img[^>]+></a>', t_result_str)
                    vid_match = re.search(r'<video[^>]+src="[^"]+/assets/[^"]+"[^>]*></video>', t_result_str)

                    if img_match:
                        img_html = img_match.group(0)
                        grid_start = '<div class="media-grid">'
                        grid_end_marker = '</div><!-- END_MEDIA_GRID -->'
                        
                        if grid_start not in reply_buf:
                            reply_buf += f"\n{grid_start}\n{img_html}\n{grid_end_marker}\n"
                        else:
                            reply_buf = reply_buf.replace(grid_end_marker, f"{img_html}\n{grid_end_marker}")

                        # Strip the raw instruction hook out of the LLM context so it doesn't try to regurgitate it
                        context_msg = "✅ 工具调用成功！图片已在界面直接展示给用户，请简要用一两句话评价生成的图片即可，绝对不要再回复任何图片链接。"
                    elif vid_match:
                        vid_html = vid_match.group(0)
                        reply_buf += f"\n<div class=\"media-video\">\n{vid_html}\n</div>\n"
                        context_msg = "✅ 工具调用成功！视频已在界面直接展示给用户，请简要用一两句话描述生成的视频内容即可，绝对不要再回复任何视频链接。"
                    else:
                        context_msg = t_result_str
                        
                    if working_card in reply_buf:
                        reply_buf = reply_buf.replace(working_card, success_card)
                        
                    yield f"event: replace_all\ndata: {_json.dumps({'text': reply_buf}, ensure_ascii=False)}\n\n"
                    
                    current_messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": t_name,
                        "content": context_msg
                    })
                
                # 携带工具结果进行下一轮请求
                continue
            else:
                # 无工具调用，流完全结束
                break

        # --- 流结束后的后处理 ---
        session_mgr.append_ai_message(reply_buf, thinking=thinking_buf)

        if session_mgr.is_first_message:
            session_mgr.mark_first_message_done()
        else:
            session_mgr.update_activity()

        # 构建 done 事件的附加数据
        done_data: dict = {}

        # 自动任务
        if task_intent:
            try:
                created = task_scheduler.create_task(
                    task_name=task_intent["task_name"],
                    trigger_type=task_intent["trigger_type"],
                    trigger_args=task_intent["trigger_args"],
                    action_prompt=task_intent["action_prompt"],
                )
                broadcast_sync({"type": "task_created", "task": created})
                session_mgr.append_event("auto_task", created)
                done_data["auto_task"] = created
            except Exception as e:
                print(f"[AutoTask] 自动创建任务失败: {e}")

        # 自动创建日程
        if schedule_intent and schedule_intent.get("action") == "create":
            try:
                sch_data = {
                    "title": schedule_intent["title"],
                    "start_time": schedule_intent["start_time"],
                    "end_time": schedule_intent.get("end_time", ""),
                    "description": schedule_intent.get("description", ""),
                    "category": schedule_intent.get("category", "其他"),
                    "location": schedule_intent.get("location", ""),
                    "all_day": schedule_intent.get("all_day", False),
                }
                # 如果没有结束时间，默认 1 小时
                if not sch_data["end_time"]:
                    from datetime import datetime as _dt, timedelta as _td
                    start = _dt.fromisoformat(sch_data["start_time"])
                    sch_data["end_time"] = (start + _td(hours=1)).isoformat()
                created_sch = schedule_mgr.create(sch_data)
                broadcast_sync({"type": "schedule_created", "schedule": created_sch})
                session_mgr.append_event("auto_schedule", created_sch)
                done_data["auto_schedule"] = created_sch
            except Exception as e:
                print(f"[AutoSchedule] 自动创建日程失败: {e}")

        # 文件搜索结果
        if search_res_json:
            session_mgr.append_event("file_search", search_res_json)
            done_data["file_search_result"] = search_res_json

        # 持久化附件
        if attachments:
            import base64 as _b64
            assets_dir = os.path.join(session_mgr.session_dir, "assets")
            os.makedirs(assets_dir, exist_ok=True)
            saved_files: list[dict] = []
            for att in attachments:
                ext = os.path.splitext(att.get("name", ""))[1] or ".bin"
                unique_name = _uuid.uuid4().hex[:8] + ext  # type: ignore[index]
                filepath = os.path.join(assets_dir, unique_name)
                try:
                    raw = _b64.b64decode(att.get("data", ""))
                    with open(filepath, "wb") as f:
                        f.write(raw)
                    saved_files.append({
                        "filename": unique_name,
                        "original_name": att.get("name", "未知"),
                        "mime": att.get("mime", "application/octet-stream"),
                        "url": f"/api/session_asset/{session_mgr.session_id}/{unique_name}",
                    })
                except Exception as e:
                    print(f"[Chat] 保存附件失败: {e}")
            if saved_files:
                session_mgr.append_event("chat_attach", {"files": saved_files}, after_msg_index=user_msg_index)
                done_data["chat_attachments"] = saved_files

        yield f"event: done\ndata: {_json.dumps(done_data, ensure_ascii=False)}\n\n"

    return StreamingResponse(_sse_generator(), media_type="text/event-stream")




# ====== 会话资源路由 ======

@app.get("/api/session_asset/{session_id}/{filename}")
def serve_session_asset(session_id: str, filename: str):
    """提供会话中保存的附件文件（图片、PDF 等）"""
    from fastapi import HTTPException  # type: ignore[import]
    filepath = os.path.join(SESSION_DIR, session_id, "assets", filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(filepath)

# ====== 定时任务路由 ======

class TaskCreateRequest(BaseModel):
    task_name: str
    trigger_type: str           # "date" | "interval" | "cron"
    trigger_args: dict
    action_prompt: str

@app.get("/api/tasks")
def get_tasks():
    return {"tasks": task_scheduler.list_tasks()}

@app.post("/api/tasks")
def create_task(req: TaskCreateRequest):
    task = task_scheduler.create_task(
        task_name=req.task_name,
        trigger_type=req.trigger_type,
        trigger_args=req.trigger_args,
        action_prompt=req.action_prompt,
    )
    return {"status": "ok", "task": task}

@app.post("/api/tasks/{task_id}/pause")
def pause_task(task_id: str):
    ok = task_scheduler.pause_task(task_id)
    return {"status": "ok" if ok else "error"}

@app.post("/api/tasks/{task_id}/resume")
def resume_task(task_id: str):
    ok = task_scheduler.resume_task(task_id)
    return {"status": "ok" if ok else "error"}

@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str):
    ok = task_scheduler.delete_task(task_id)
    return {"status": "ok" if ok else "error"}

# ====== 日程管理路由 ======

class ScheduleCreateRequest(BaseModel):
    title: str
    start_time: str
    end_time: str
    description: str = ""
    all_day: bool = False
    category: str = "其他"
    color: str = ""
    location: str = ""
    rrule: str = ""
    reminder_minutes: int = 15

class ScheduleUpdateRequest(BaseModel):
    title: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    description: Optional[str] = None
    all_day: Optional[bool] = None
    category: Optional[str] = None
    color: Optional[str] = None
    location: Optional[str] = None
    rrule: Optional[str] = None
    reminder_minutes: Optional[int] = None
    status: Optional[str] = None

class ScheduleParseRequest(BaseModel):
    text: str

@app.get("/api/schedules")
def list_schedules(start: str = Query(""), end: str = Query("")):
    """按时间范围查询日程"""
    if not start or not end:
        return {"schedules": schedule_mgr.list_all()}
    return {"schedules": schedule_mgr.list_range(start, end)}

@app.post("/api/schedules")
def create_schedule(req: ScheduleCreateRequest):
    """创建日程"""
    schedule = schedule_mgr.create(req.model_dump())
    return {"status": "ok", "schedule": schedule}

@app.put("/api/schedules/{schedule_id}")
def update_schedule(schedule_id: str, req: ScheduleUpdateRequest):
    """更新日程"""
    data = {k: v for k, v in req.model_dump().items() if v is not None}
    result = schedule_mgr.update(schedule_id, data)
    if result is None:
        return {"status": "error", "message": "日程不存在"}
    return {"status": "ok", "schedule": result}

@app.delete("/api/schedules/{schedule_id}")
def delete_schedule(schedule_id: str):
    """删除日程"""
    ok = schedule_mgr.delete(schedule_id)
    return {"status": "ok" if ok else "error"}

@app.get("/api/schedules/today")
def get_today_briefing():
    """获取今日日程摘要"""
    briefing = schedule_mgr.generate_daily_briefing()
    from datetime import date as _date
    today = _date.today().isoformat()
    schedules = schedule_mgr.list_range(today, today)
    return {"briefing": briefing, "schedules": schedules}

@app.post("/api/schedules/parse")
def parse_schedule_text(req: ScheduleParseRequest):
    """AI 解析自然语言为日程 JSON"""
    result = schedule_mgr.parse_natural_language(req.text)
    if result:
        return {"status": "ok", "schedule_data": result}
    return {"status": "error", "message": "无法解析日程信息"}

# ====== 通知通道路由 ======

@app.get("/api/notifications")
def get_notification_config():
    """获取当前通知通道配置"""
    config = load_notification_config()
    # 脱敏 app_secret
    channels = config.get("channels", {})
    dt = channels.get("dingtalk", {})
    secret = dt.get("app_secret", "")
    if secret and len(secret) > 10:
        dt["app_secret_masked"] = f"{secret[:6]}...{secret[-4:]}"
    else:
        dt["app_secret_masked"] = "未配置"
    dt.pop("app_secret", None)
    return {"config": config}

class NotificationConfigRequest(BaseModel):
    channels: dict

@app.post("/api/notifications")
def update_notification_config(req: NotificationConfigRequest):
    """更新通知通道配置（重启后生效）"""
    current = load_notification_config()
    for ch_name, ch_cfg in req.channels.items():
        if ch_name in current["channels"]:
            current["channels"][ch_name].update(ch_cfg)
        else:
            current["channels"][ch_name] = ch_cfg
    save_notification_config(current)
    return {"status": "ok", "message": "配置已保存，重启服务后生效"}

@app.post("/api/notifications/test")
def test_notification():
    """发送测试消息到钉钉，验证通道连通性"""
    if notification_mgr is None:
        return {"status": "error", "message": "通知管理器未初始化"}
    dt_channel = notification_mgr.get_channel("dingtalk")
    if dt_channel is None or not dt_channel.enabled:
        return {"status": "error", "message": "钉钉通道未启用"}
    # DingTalkChannel 类型断言
    from backend.notification_manager import DingTalkChannel as _DT  # type: ignore[import]
    if isinstance(dt_channel, _DT):
        ok = dt_channel.test_send()
        if ok:
            return {"status": "ok", "message": "测试消息已发送，请检查钉钉"}
        return {"status": "error", "message": "发送失败，请检查配置和日志"}
    return {"status": "error", "message": "通道类型错误"}

# ====== 文件管理路由 ======

@app.post("/api/files/upload")
async def upload_file(
    file: UploadFile = File(...),
    description: str = Form(""),
):
    """上传文件到 MinIO（快速返回，AI 标签后台异步生成）"""
    if not minio_mgr.enabled:
        return {"status": "error", "message": "MinIO 未配置"}
    try:
        data = await file.read()
        filename = file.filename or "unnamed"
        content_type = file.content_type or "application/octet-stream"

        # 1. 快速上传，立即返回
        entry = minio_mgr.upload_fast(
            filename=filename,
            file_data=data,
            content_type=content_type,
            description=description,
        )

        # 2. 后台线程处理元信息提取 + AI 打标签
        import threading
        object_name = entry["object_name"]

        def _background_tagging():
            try:
                result = minio_mgr.process_tags(
                    object_name=object_name,
                    filename=filename,
                    content_type=content_type,
                    description=description,
                )
                # 通过 WebSocket 推送标签就绪通知
                broadcast_sync({
                    "type": "file_tags_ready",
                    "object_name": object_name,
                    "original_name": filename,
                    "tags": result["tags"],
                    "categorized_tags": result.get("categorized_tags", {}),
                    "file_meta": result.get("file_meta", {}),
                })
            except Exception as e:
                print(f"[AsyncTag] 后台标签处理失败: {e}")
                broadcast_sync({
                    "type": "file_tags_ready",
                    "object_name": object_name,
                    "original_name": filename,
                    "tags": [],
                    "error": str(e),
                })

        threading.Thread(target=_background_tagging, daemon=True).start()

        return {"status": "ok", "file": entry}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/files/upload_batch")
async def upload_files_batch(
    files: list[UploadFile] = File(...),
    description: str = Form(""),
):
    """批量上传多个文件到 MinIO（快速返回，AI 标签后台异步生成）"""
    if not minio_mgr.enabled:
        return {"status": "error", "message": "MinIO 未配置"}
    if not files:
        return {"status": "error", "message": "未选择任何文件"}

    try:
        # 1. 读取所有文件数据
        file_tuples: list[tuple[str, bytes, str, str]] = []
        for f in files:
            data = await f.read()
            file_tuples.append((
                f.filename or "unnamed",
                data,
                f.content_type or "application/octet-stream",
                description,
            ))

        # 2. 批量快速上传（一次性写索引）
        entries = minio_mgr.upload_batch(file_tuples)

        # 3. 后台线程逐个处理元信息 + AI 标签
        import threading

        def _background_batch_tagging():
            for i, entry in enumerate(entries):
                try:
                    result = minio_mgr.process_tags(
                        object_name=entry["object_name"],
                        filename=entry["original_name"],
                        content_type=entry["content_type"],
                        description=entry.get("description", ""),
                    )
                    broadcast_sync({
                        "type": "file_tags_ready",
                        "object_name": entry["object_name"],
                        "original_name": entry["original_name"],
                        "tags": result["tags"],
                        "categorized_tags": result.get("categorized_tags", {}),
                        "file_meta": result.get("file_meta", {}),
                        "batch_progress": f"{i + 1}/{len(entries)}",
                    })
                except Exception as e:
                    print(f"[AsyncTag] 批量标签处理失败 ({entry['original_name']}): {e}")
                    broadcast_sync({
                        "type": "file_tags_ready",
                        "object_name": entry["object_name"],
                        "original_name": entry["original_name"],
                        "tags": [],
                        "error": str(e),
                        "batch_progress": f"{i + 1}/{len(entries)}",
                    })

        threading.Thread(target=_background_batch_tagging, daemon=True).start()

        return {
            "status": "ok",
            "total": len(file_tuples),
            "uploaded": len(entries),
            "failed": len(file_tuples) - len(entries),
            "files": entries,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/files/search")
def search_files(q: str = Query("")):
    """搜索文件（按文件名/描述/标签）"""
    results = minio_mgr.search(q)
    # 为每个结果生成下载链接
    for r in results:
        r["download_url"] = minio_mgr.get_download_url(r["object_name"])
    return {"files": results}

class AISearchRequest(BaseModel):
    prompt: str

@app.post("/api/files/ai_search")
def ai_search_files(req: AISearchRequest):
    """AI 语义检索文件（基于标签池匹配）"""
    if not minio_mgr.enabled:
        return {"status": "error", "message": "MinIO 未配置"}
    try:
        result = minio_mgr.ai_search(req.prompt)
        return result
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/files/tags")
def get_file_tags():
    """获取全局标签池（按分类汇总）"""
    if not minio_mgr.enabled:
        return {"status": "error", "message": "MinIO 未配置"}
    return {"status": "ok", "tags": minio_mgr.get_tag_pool()}

@app.get("/api/files")
def list_files():
    """列出所有文件"""
    files = minio_mgr.list_files()
    for f in files:
        f["download_url"] = minio_mgr.get_download_url(f["object_name"])
    return {"files": files}

@app.delete("/api/files/{object_name:path}")
def delete_file(object_name: str):
    """删除文件"""
    ok = minio_mgr.delete(object_name)
    if ok:
        return {"status": "ok"}
    return {"status": "error", "message": "删除失败"}

# ====== 音乐功能路由 ======

class PlaylistRequest(BaseModel):
    prompt: str

@app.get("/api/music")
def list_music():
    """列出所有音频文件"""
    try:
        audio_files = minio_mgr.search_audio()
        for f in audio_files:
            f["download_url"] = minio_mgr.get_download_url(f["object_name"])

        # 检查是否有音频缺封面，有则后台触发重索引
        try:
            needs_reindex = any(
                not (f.get("file_meta") or {}).get("cover_art")
                for f in audio_files
            )
            if needs_reindex:
                import threading
                threading.Thread(target=minio_mgr.reindex_cover_art, daemon=True).start()
        except Exception:
            pass

        return {"songs": audio_files}
    except Exception as e:
        return {"songs": [], "error": str(e)}

@app.get("/api/music/search")
def search_music(q: str = Query("")):
    """搜索音乐（复用文件管理搜索，过滤只返回音频）"""
    results = minio_mgr.search(q)
    audio_results = [
        f for f in results
        if minio_mgr._is_audio(f.get("content_type", ""), f.get("original_name", ""))
    ]
    for f in audio_results:
        f["download_url"] = minio_mgr.get_download_url(f["object_name"])
    return {"songs": audio_results}

@app.post("/api/music/reindex")
def reindex_music_cover():
    """手动触发封面重新提取"""
    if not minio_mgr.enabled:
        return {"status": "error", "message": "MinIO 未配置"}
    try:
        updated = minio_mgr.reindex_cover_art()
        return {"status": "ok", "updated": updated, "message": f"已更新 {updated} 个文件的封面"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/music/playlist")
def generate_playlist(req: PlaylistRequest):
    """AI 智能生成歌单"""
    if not minio_mgr.enabled:
        return {"status": "error", "message": "MinIO 未配置"}
    try:
        playlist = minio_mgr.generate_playlist(req.prompt)
        return {"status": "ok", "playlist": playlist}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/music/cover/{object_name:path}")
def get_music_cover(object_name: str):
    """获取音频文件的封面图片"""
    import os
    from fastapi import HTTPException  # type: ignore[import]
    from fastapi.responses import FileResponse  # type: ignore[import]

    if not minio_mgr.enabled:
        raise HTTPException(status_code=404, detail="MinIO 未配置")
    
    cover_path = minio_mgr.get_cover_art_file(object_name)
    if cover_path and os.path.exists(cover_path):
        return FileResponse(cover_path)
    
    raise HTTPException(status_code=404, detail="Cover art not found")

@app.get("/api/music/lyrics/{object_name:path}")
def get_music_lyrics(object_name: str):
    """获取音频文件的歌词"""
    from fastapi import HTTPException  # type: ignore[import]
    if not minio_mgr.enabled:
        raise HTTPException(status_code=404, detail="MinIO 未配置")
    
    lyrics = minio_mgr.get_lyrics(object_name)
    return {"lyrics": lyrics}

@app.get("/api/music/stream/{object_name:path}")
def stream_music(object_name: str):
    """获取音频流 URL（重定向到预签名链接）"""
    url = minio_mgr.get_download_url(object_name)
    if not url:
        return {"status": "error", "message": "获取播放链接失败"}
    from fastapi.responses import RedirectResponse  # type: ignore[import]
    return RedirectResponse(url=url)

# ====== WebSocket 端点 ======

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    print(f"[WS] 新客户端连接，当前 {len(ws_clients)} 个")
    try:
        while True:
            # 保持连接活跃，同时处理客户端心跳 ping
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        ws_clients.discard(ws)
        print(f"[WS] 客户端断开，剩余 {len(ws_clients)} 个")
    except Exception:
        ws_clients.discard(ws)


# ====== 静态文件挂载（放在最后，避免拦截 API 路由） ======
os.makedirs(SESSION_DIR, exist_ok=True)
app.mount("/assets", StaticFiles(directory=SESSION_DIR), name="assets")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
