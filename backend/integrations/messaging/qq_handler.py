"""
qq_handler.py — QQ 聊天处理器 (基于 NapCat / OneBot 11 反向 WebSocket)

支持命令：-new / -history / 普通 AI 对话
完全对标 dingtalk_handler.py 的实现。
"""
import json
import logging
from typing import Optional

from backend.config import (  # type: ignore[import]
    SYSTEM_PROMPT, MEMORY_FILE,
    get_client, APP_SETTINGS,
)
from backend.ai.session_manager import SessionManager  # type: ignore[import]
from backend.ai.rag_engine import RAGEngine  # type: ignore[import]
from backend.integrations.mcp import mcp_mgr  # type: ignore[import]

logger = logging.getLogger(__name__)


# ====== 工具函数 ======

def _load_memory() -> str:
    """读取 memory.md 的内容"""
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
        return content if content and content != "# 长期记忆" else ""
    except FileNotFoundError:
        return ""


class QQChatHandler:
    """
    QQ 消息处理器 — 每个用户独立会话。

    由 server.py 中的 /ws/qq WebSocket 端点调用。
    """

    def __init__(self, rag: Optional[RAGEngine] = None, minio_mgr=None):
        # user_id → SessionManager (每用户独立)
        self.user_sessions: dict[str, SessionManager] = {}
        self.rag = rag or RAGEngine()
        self.minio_mgr = minio_mgr
        self._task_scheduler: Optional[object] = None  # 由 server.py 注入
        self._qq_channel = None  # QQChannel 实例，用于回复消息

    def set_task_scheduler(self, scheduler):
        """注入任务调度器"""
        self._task_scheduler = scheduler

    def set_qq_channel(self, channel):
        """注入 QQChannel 实例，用于主动回复"""
        self._qq_channel = channel

    def _get_scheduler(self):
        """获取任务调度器"""
        return self._task_scheduler

    def _get_session(self, user_id: str) -> SessionManager:
        """获取或创建用户的 SessionManager"""
        if user_id not in self.user_sessions:
            sm = SessionManager()
            self.user_sessions[user_id] = sm
            logger.info(f"[QQ] 为用户 {user_id} 创建了新会话")
        return self.user_sessions[user_id]

    async def handle_onebot_event(self, event: dict) -> None:
        """
        处理 OneBot 11 上报事件。

        Args:
            event: OneBot 11 标准事件 dict
        """
        post_type = event.get("post_type")

        if post_type == "message":
            await self._handle_message_event(event)
        elif post_type == "meta_event":
            # 心跳 / 生命周期事件，仅记录
            meta_type = event.get("meta_event_type", "")
            if meta_type == "lifecycle":
                sub_type = event.get("sub_type", "")
                logger.info(f"[QQ] 生命周期事件: {sub_type}")
            # heartbeat 不记日志，太频繁
        else:
            logger.debug(f"[QQ] 忽略事件类型: {post_type}")

    async def _handle_message_event(self, event: dict) -> None:
        """处理消息事件"""
        message_type = event.get("message_type")  # "private" | "group"
        raw_message = event.get("raw_message", "").strip()
        user_id = str(event.get("user_id", "unknown"))
        group_id = event.get("group_id")
        sender = event.get("sender", {})
        sender_name = sender.get("nickname", user_id)

        if not raw_message:
            return

        logger.info(
            f"[QQ] 收到{('群' if message_type == 'group' else '私聊')}消息 "
            f"[{sender_name}] (userId={user_id}): {raw_message[:100]}"
        )

        # 群聊中需要 @机器人 才响应（检查 CQ 码中是否 at 了自己）
        # 如果是群聊但没 @ 机器人，忽略
        self_id = event.get("self_id")
        if message_type == "group":
            # 检查是否 @了机器人
            message_segments = event.get("message", [])
            is_at_me = False
            if isinstance(message_segments, list):
                for seg in message_segments:
                    if seg.get("type") == "at" and str(seg.get("data", {}).get("qq")) == str(self_id):
                        is_at_me = True
                        break
            if not is_at_me:
                return  # 群聊中没 @ 机器人，不响应

            # 从消息中去掉 @机器人 的 CQ 码
            import re
            raw_message = re.sub(r'\[CQ:at,qq=\d+\]\s*', '', raw_message).strip()
            if not raw_message:
                return

        # 自动注册用户 ID
        self._auto_register_user(user_id)

        # ========== 命令分发 ==========
        if raw_message.lower() == "-new":
            reply = self._cmd_new(user_id)
        elif raw_message.lower() == "-history":
            reply = self._cmd_history(user_id)
        else:
            reply = self._chat(user_id, raw_message)

        # 发送回复
        self._reply(message_type, user_id, group_id, reply)

    def _reply(self, message_type: str, user_id: str, group_id: Optional[int], text: str):
        """通过 QQChannel 发送回复"""
        channel = self._qq_channel
        if channel is None:
            logger.warning("[QQ] 无 QQChannel 实例，无法回复")
            return

        try:
            if message_type == "group" and group_id:
                channel._post_send_msg("group", message=text, group_id=group_id)
            else:
                channel._post_send_msg("private", message=text, user_id=int(user_id))
        except Exception as e:
            logger.error(f"[QQ] 回复失败: {e}")

    def _auto_register_user(self, user_id: str):
        """自动把用户的 QQ 号记录到 notification.json"""
        if not user_id or user_id == "unknown":
            return
        try:
            uid_int = int(user_id)
            from backend.config import load_notification_config, save_notification_config  # type: ignore[import]
            config = load_notification_config()
            qq_cfg = config.get("channels", {}).get("qq", {})
            current_ids: list = qq_cfg.get("target_user_ids", [])
            if uid_int not in current_ids:
                current_ids.append(uid_int)
                qq_cfg["target_user_ids"] = current_ids
                config["channels"]["qq"] = qq_cfg
                save_notification_config(config)
                logger.info(f"[QQ] ✅ 已自动注册用户 {user_id} 到通知推送列表")
        except Exception as e:
            logger.error(f"[QQ] 自动注册用户失败: {e}")

    # ========== 命令实现 ==========

    def _cmd_new(self, user_id: str) -> str:
        """开启新会话"""
        sm = self._get_session(user_id)
        sm.create_new()
        logger.info(f"[QQ] 用户 {user_id} 开启新会话 {sm.session_id}")
        return "✅ 新会话已开启！可以开始聊天了~"

    def _cmd_history(self, user_id: str) -> str:
        """查看历史会话列表"""
        sm = self._get_session(user_id)
        sessions = sm.list_all()

        if not sessions:
            return "📭 暂无历史会话记录"

        lines = ["📋 最近的会话记录：", ""]
        for i, s in enumerate(sessions[:10], 1):
            active = " 👈 当前" if s.get("active") else ""
            name = s.get("name", "未命名")
            last = s.get("last_active", "")[:16]
            lines.append(f"{i}. {name} ({last}){active}")

        return "\n".join(lines)

    def _chat(self, user_id: str, user_text: str) -> str:
        """普通 AI 对话（具备多轮工具调用能力的 PAO 循环）"""
        import json as _json
        import time as _time
        from datetime import datetime as _dt
        from backend.integrations.mcp import mcp_mgr

        sm = self._get_session(user_id)
        turn_number = sm.get_turn_number()

        # --- 统一意图分析（RAG + 定时任务 + MCP） ---
        intent = self.rag.analyze_intent(sm.messages, user_text)
        task_intent = intent["task_intent"]
        mcp_intent = intent.get("mcp_intent", "NONE")

        # --- 第一条消息时初始化文件 ---
        if sm.is_first_message:
            sm.initialize_file()

        # --- 写入用户消息 ---
        sm.append_user_message(user_text)

        # 记录到 ai_context
        sm.append_ai_context({
            "turn": turn_number,
            "role": "user",
            "content": user_text[:500],
            "ts": _dt.now().isoformat(),
        })

        # --- 构建 AI 消息列表 ---
        ai_messages = [m for m in sm.messages if m["role"] in ("system", "user", "assistant")]

        # 注入当前时间
        now_str = _dt.now().strftime("%Y年%m月%d日 %H:%M（%A）")
        ai_messages.insert(1, {
            "role": "system",
            "content": f"当前时间：{now_str}",
        })

        # 注入长期记忆
        memory_text = _load_memory()
        if memory_text:
            ai_messages.insert(1, {
                "role": "system",
                "content": f"以下是用户的长期记忆档案，请在回答时参考：\n{memory_text}",
            })

        # --- 准备工具列表 ---
        mcp_tools = mcp_mgr.get_all_tools_for_loop(mcp_intent)
        builtin_tools = [
            {
                "type": "function",
                "function": {
                    "name": "_builtin_rag_search",
                    "description": "搜索用户私有的本地知识库（仅包含用户主动上传的笔记、课件、技术文档等参考资料）。注意：这不是互联网搜索，仅当用户明确要求查询自己上传的资料时才调用。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "搜索关键词，3-5 个核心词"}
                        },
                        "required": ["query"]
                    }
                }
            }
        ]
        if self.minio_mgr and self.minio_mgr.enabled:
            builtin_tools.append({
                "type": "function",
                "function": {
                    "name": "_builtin_file_search",
                    "description": "在云端网盘中搜索用户的文件、照片、文档、音乐等。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "文件查找描述，如'去年的旅行照片'、'工作周报'"}
                        },
                        "required": ["query"]
                    }
                }
            })

        all_tools = builtin_tools + (mcp_tools if mcp_tools else [])

        # 注入系统工具指引
        if all_tools:
            MAX_LOOPS = int(APP_SETTINGS.get("max_tool_loops", 6))
            ai_messages.insert(0, {
                "role": "system",
                "content": (
                    "你拥有使用外部工具的能力。\n"
                    "【Plan→Act→Observe 工作流】\n"
                    "每次回复时，如果需要调用工具：\n"
                    "1. 先进行一段简短的“💡 分析：...”\n"
                    "2. 然后调用相应工具。\n"
                    f"最多可进行 {MAX_LOOPS} 轮工具调用。\n"
                    "若工具生成了媒体（如画图、音乐），系统会在后台转为 CQ 码自动展现给用户，你直接总结即可，绝对不要在文本中输出 URL 或 Markdown 形式的媒体链接。"
                )
            })
        else:
            MAX_LOOPS = 1

        provider = APP_SETTINGS.get("chat_provider", "deepseek")
        client = get_client(provider)
        model = APP_SETTINGS.get("chat_model", "deepseek-chat")

        final_reply_texts = []
        search_res_json = None

        for loop_round in range(1, MAX_LOOPS + 1):
            remaining = MAX_LOOPS - loop_round + 1

            if loop_round > 1:
                sm.append_ai_context({
                    "turn": turn_number,
                    "role": "plan",
                    "loop": loop_round,
                    "remaining": remaining,
                    "has_tools": bool(all_tools),
                    "ts": _dt.now().isoformat(),
                })
                ai_messages.append({
                    "role": "system",
                    "content": f"\n🔍 Observe — 第 {loop_round} 轮（剩余 {remaining} 步）。请分析上一轮返回结果，再决定调用工具还是直接回复用户。"
                })

            call_kwargs = {
                "model": model,
                "messages": ai_messages,
                "temperature": 0.7,
                "stream": False,
            }
            if all_tools:
                call_kwargs["tools"] = all_tools

            try:
                response = client.chat.completions.create(**call_kwargs)
            except Exception as e:
                # 兼容不支持工具的模型降级
                if "400" in str(e) and "tools" in call_kwargs:
                    logger.warning("[QQ] 模型不支持 tools 参数，降级为无工具模式")
                    call_kwargs.pop("tools", None)
                    all_tools = []
                    try:
                        response = client.chat.completions.create(**call_kwargs)
                    except Exception as e2:
                        sm.pop_last_message()
                        logger.error(f"[QQ] AI 降级调用失败: {e2}")
                        return f"😵 AI 出错了：{str(e2)[:200]}"
                else:
                    sm.pop_last_message()
                    logger.error(f"[QQ] AI 调用失败: {e}")
                    return f"😵 AI 出错了：{str(e)[:200]}"

            message = response.choices[0].message
            content = message.content or ""
            
            # TODO: detect "reasoning_content" if present
            reasoning = getattr(message, "reasoning_content", None) or ""
            # QQ端通常不发送思考链，可由用户偏好决定。这里为了整洁暂时过滤掉 deepseek-r1 的推理。

            if content:
                # 去掉可能存在的内部 HTML 思考或媒体标记
                import re as _re_strip
                _clean_content = _re_strip.sub(r'<[^>]+>', '', content)
                if _clean_content.strip():
                    final_reply_texts.append(_clean_content.strip())

            tool_calls = getattr(message, "tool_calls", None)
            if not tool_calls:
                break  # 没有工具调用，循环结束

            # 保存 AI 思考上下文以便带入下一轮
            msg_dict = {"role": "assistant"}
            if content:
                msg_dict["content"] = content
            tool_calls_list = []
            for tc in tool_calls:
                tool_calls_list.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments}
                })
            msg_dict["tool_calls"] = tool_calls_list
            ai_messages.append(msg_dict)

            # 逐个执行工具
            for tc in tool_calls:
                t_name = tc.function.name
                try:
                    args_dict = _json.loads(tc.function.arguments)
                except:
                    args_dict = {}

                t_start = _time.time()
                t_result = ""

                if t_name == "_builtin_rag_search":
                    q = args_dict.get("query", "")
                    rag_res = self.rag.retrieve_context(q, top_k=3)
                    t_result = "知识库检索结果：\n" + (rag_res if rag_res else "未找到相关内容。")
                elif t_name == "_builtin_file_search":
                    q = args_dict.get("query", "")
                    if self.minio_mgr:
                        _search = self.minio_mgr.ai_search(q)
                        if _search.get("status") == "ok" and _search.get("files"):
                            search_res_json = _search
                            t_result = "已找到文件，系统会自动在回复末尾添加文件列表。请简短回复。"
                        else:
                            t_result = "未找到相关文件。"
                else:
                    _TOOL_TO_INTENT = {
                        "web_search": "WEB_SEARCH", "webSearchPro": "WEB_SEARCH", "webSearchStd": "WEB_SEARCH",
                        "jimeng_image_generation": "JIMENG", "jimeng_video_generation": "JIMENG",
                        "modelstudio_z_image_generation": "Z_IMAGE",
                        "amap_poi_search": "AMAP", "maps_weather": "AMAP", "qwen_tts": "TTS",
                    }
                    t_intent = _TOOL_TO_INTENT.get(t_name, mcp_intent)
                    
                    t_result = mcp_mgr.execute_tool(
                        intent=t_intent,
                        tool_name=t_name,
                        args=args_dict,
                        session_id=sm.session_id,
                        session_dir=sm.session_dir
                    )

                # 将工具执行结果记录到 AI 上下文
                t_result_str = str(t_result)
                
                # 拦截 HTML 图片并转换为 CQ 码
                import re as _cq_re
                img_matches = _cq_re.findall(r'href="(/assets/[^"]+)"', t_result_str)
                for _img_path in img_matches:
                    cq_code = f"[CQ:image,file=http://127.0.0.1:8000{_img_path}]"
                    final_reply_texts.append(cq_code)
                
                vid_matches = _cq_re.findall(r'src="(/assets/[^"]+\.mp4)"', t_result_str)
                for _vid_path in vid_matches:
                    cq_code = f"[CQ:video,file=http://127.0.0.1:8000{_vid_path}]"
                    final_reply_texts.append(cq_code)

                # 修改给AI看的结果，避免AI复读HTML
                if img_matches or vid_matches:
                    t_result_str = "\n[系统提示] 工具调用成功，媒体内容已通过CQ码直接发给用户。请你在回复中简要总结结果，绝对不要输出任何图片或视频链接！\n" + _cq_re.sub(r'<[^>]+>', '', t_result_str)

                ai_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": t_name,
                    "content": t_result_str
                })

                t_dur = int((_time.time() - t_start) * 1000)
                _info = _cq_re.sub(r'<[^>]+>', '', t_result_str)[:300].strip()
                sm.append_ai_context({
                    "turn": turn_number,
                    "role": "observation",
                    "loop": loop_round,
                    "source": t_name,
                    "info": _info if _info else "媒体已生成",
                    "duration_ms": t_dur,
                    "ts": _dt.now().isoformat(),
                })

        # --- 循环结束，组装最终内容 ---
        ai_reply = "\n\n".join(final_reply_texts)

        # 清除最终回复中多余的思考链（部分小模型会漏掉）
        import re as _clean_re
        ai_reply = _clean_re.sub(r'💡 分析：.*?\n', '', ai_reply)
        ai_reply = _clean_re.sub(r'🔍 Observe —.*?\n', '', ai_reply)

        # 写入历史并更新状态
        sm.append_ai_message(ai_reply)
        if sm.is_first_message:
            sm.mark_first_message_done()
        else:
            sm.update_activity()

        # 追加网盘搜索结果并实体发送
        if search_res_json:
            found_files = []
            # 限制最多发送前3个最匹配的文件，防止刷屏
            for f in search_res_json["files"][:3]:
                # 获取有效期的预签名URL
                url = self.minio_mgr.get_download_url(f["object_name"]) if self.minio_mgr else ""
                
                cq_code = ""
                if url:
                    ext = f["original_name"].split(".")[-1].lower() if "." in f["original_name"] else ""
                    if ext in ["png", "jpg", "jpeg", "gif", "webp"]:
                        cq_code = f"[CQ:image,file={url}]"
                    elif ext in ["mp4", "mov", "avi"]:
                        cq_code = f"[CQ:video,file={url}]"
                    elif ext in ["mp3", "wav", "m4a", "ogg"]:
                        cq_code = f"[CQ:record,file={url}]"
                    else:
                        cq_code = f"[CQ:file,file={url},name={f['original_name']}]"
                
                # 在文本列表中展示，同时附带 CQ 码触发底层发送
                found_files.append(f"- {f['original_name']} {cq_code}".strip())

            reason_text = f"💡 匹配原因：{search_res_json.get('reason', '')}\n" if search_res_json.get('reason') else ""
            extra = f"\n\n---\n📁 为您发送最匹配的文件：\n{reason_text}" + "\n".join(found_files)
            ai_reply += extra

        # 创建定时任务
        if task_intent:
            try:
                scheduler = self._get_scheduler()
                if scheduler:
                    created = scheduler.create_task(
                        task_name=task_intent["task_name"],
                        trigger_type=task_intent["trigger_type"],
                        trigger_args=task_intent["trigger_args"],
                        action_prompt=task_intent["action_prompt"],
                    )
                    task_info = f"\n\n✅ 已创建定时任务「{created['task_name']}」({created['trigger_type']})"
                    ai_reply += task_info
                    logger.info(f"[QQ] 自动创建任务: {created['task_name']}")
            except Exception as e:
                logger.error(f"[QQ] 自动创建任务失败: {e}")

        return ai_reply.strip()
