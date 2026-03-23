"""
dingtalk_handler.py — 钉钉聊天处理器 (Stream 模式)
支持命令：-new / -history / 普通 AI 对话
"""
import logging
from typing import Optional

from backend.config import (  # type: ignore[import]
    SYSTEM_PROMPT, MEMORY_FILE,
    get_client, APP_SETTINGS,
)
from backend.session_manager import SessionManager  # type: ignore[import]
from backend.rag_engine import RAGEngine  # type: ignore[import]

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


class DingTalkChatHandler:
    """
    钉钉消息处理器 — 每个用户独立会话。

    需要在 DingTalkChannel.start() 中被包装为 ChatbotHandler 子类并注册到 Stream 客户端。
    """

    def __init__(self, rag: Optional[RAGEngine] = None, minio_mgr = None):
        # user_id → SessionManager (每用户独立)
        self.user_sessions: dict[str, SessionManager] = {}
        self.rag = rag or RAGEngine()
        self.minio_mgr = minio_mgr
        self._task_scheduler: Optional[object] = None  # 由 server.py 注入

    def set_task_scheduler(self, scheduler):
        """注入任务调度器（由 server.py 在 lifespan 中调用）"""
        self._task_scheduler = scheduler

    def _get_scheduler(self):
        """获取任务调度器"""
        return self._task_scheduler

    def _get_session(self, user_id: str) -> SessionManager:
        """获取或创建用户的 SessionManager"""
        if user_id not in self.user_sessions:
            sm = SessionManager()
            self.user_sessions[user_id] = sm
            logger.info(f"[DingTalk] 为用户 {user_id} 创建了新会话")
        return self.user_sessions[user_id]

    async def handle_message(self, msg, reply_fn) -> None:
        """
        处理钉钉消息的核心逻辑。

        Args:
            msg: ChatbotMessage 实例
            reply_fn: 回复函数 reply_fn(text, msg)
        """
        user_text = msg.text.content.strip()
        user_id = msg.sender_staff_id or msg.sender_nick or "unknown"
        sender_name = getattr(msg, "sender_nick", user_id)

        logger.info(f"[DingTalk] 收到消息 [{sender_name}] (userId={user_id}): {user_text[:100]}")

        # 自动记录用户 ID 到通知配置（解决 user_ids 配错的问题）
        self._auto_register_user(user_id)

        # ========== 命令分发 ==========

        if user_text.lower() == "-new":
            reply = self._cmd_new(user_id)
        elif user_text.lower() == "-history":
            reply = self._cmd_history(user_id)
        else:
            reply = self._chat(user_id, user_text)

        # 发送回复
        try:
            reply_fn(reply, msg)
        except Exception as e:
            logger.error(f"[DingTalk] 回复失败: {e}")

    def _auto_register_user(self, user_id: str):
        """自动把用户的正确 userId 记录到 notification.json"""
        if not user_id or user_id == "unknown":
            return
        try:
            from backend.config import load_notification_config, save_notification_config  # type: ignore[import]
            config = load_notification_config()
            dt_cfg = config.get("channels", {}).get("dingtalk", {})
            current_ids: list = dt_cfg.get("user_ids", [])
            if user_id not in current_ids:
                current_ids.append(user_id)
                dt_cfg["user_ids"] = current_ids
                config["channels"]["dingtalk"] = dt_cfg
                save_notification_config(config)
                logger.info(f"[DingTalk] ✅ 已自动注册用户 {user_id} 到通知推送列表")
        except Exception as e:
            logger.error(f"[DingTalk] 自动注册用户失败: {e}")

    # ========== 命令实现 ==========

    def _cmd_new(self, user_id: str) -> str:
        """开启新会话"""
        sm = self._get_session(user_id)
        sm.create_new()
        logger.info(f"[DingTalk] 用户 {user_id} 开启新会话 {sm.session_id}")
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
        """普通 AI 对话（复用 Web 端完整链路）"""
        sm = self._get_session(user_id)

        # --- 统一意图分析（RAG + 定时任务） ---
        intent = self.rag.analyze_intent(sm.messages, user_text)
        rag_context = self.rag.retrieve_context(intent["rag_query"])
        task_intent = intent["task_intent"]
        file_search_query = intent.get("file_search_query")

        # --- 第一条消息时初始化文件 ---
        if sm.is_first_message:
            sm.initialize_file()

        # --- 写入用户消息 ---
        sm.append_user_message(user_text)

        # --- 构建 AI 消息列表（过滤掉 API 不认识的 role） ---
        ai_messages = [m for m in sm.messages if m["role"] in ("system", "user", "assistant")]

        # 注入长期记忆
        memory_text = _load_memory()
        if memory_text:
            ai_messages.insert(1, {
                "role": "system",
                "content": f"以下是用户的长期记忆档案，请在回答时参考：\n{memory_text}",
            })

        # 注入 RAG 上下文
        if rag_context:
            ai_messages.insert(-1, {
                "role": "system",
                "content": f"以下是从本地知识库检索到的参考资料：{rag_context}",
            })

        # 注入查找文件上下文
        search_res_json = None
        if file_search_query and self.minio_mgr:
            search_res = self.minio_mgr.ai_search(file_search_query)
            if search_res.get("status") == "ok" and search_res.get("files"):
                search_res_json = search_res
                context_msg = f"系统已经根据用户的查找意图【{file_search_query}】从网盘中找出了相关文件。匹配原因：{search_res.get('reason', '')}。\n请你仅用一两句亲切的话告知用户找到了文件，**绝对不要**在回复中列出文件的名称或下载链接（系统会自动在你的回复之后拼接这些列表）。"
                ai_messages.insert(-1, {"role": "system", "content": context_msg})
            else:
                ai_messages.insert(-1, {"role": "system", "content": f"系统试图在网盘中查找描述为“{file_search_query}”的文件，但未能找到任何匹配项。请告知用户。"})

        # --- 调用 AI ---
        try:
            from backend.config import API_PROVIDERS  # type: ignore[import]
            first_provider = list(API_PROVIDERS.keys())[0] if API_PROVIDERS else "deepseek"
            client = get_client(first_provider)
            model = API_PROVIDERS[first_provider]["models"][0]["id"]

            response = client.chat.completions.create(
                model=model,
                messages=ai_messages,
                temperature=0.7,
                stream=False,
            )
            ai_reply = response.choices[0].message.content
        except Exception as e:
            sm.pop_last_message()
            logger.error(f"[DingTalk] AI 调用失败: {e}")
            return f"😵 AI 出错了：{str(e)[:200]}"  # type: ignore[index]

        # --- 写入 AI 回复 ---
        sm.append_ai_message(ai_reply)

        # --- 更新元数据 ---
        if sm.is_first_message:
            sm.mark_first_message_done()
        else:
            sm.update_activity()

        # 追加找到的文件到回复列表中
        if search_res_json:
            found_files = "\n".join([f"- {f['original_name']} [下载/查看]({f['download_url']})" for f in search_res_json["files"]])
            reason_text = f"💡 匹配原因：{search_res_json.get('reason', '')}\n" if search_res_json.get('reason') else ""
            extra_ui = f"\n\n---\n📁 **已找到的文件：**\n{reason_text}{found_files}"
            ai_reply += extra_ui

        # --- 自主创建定时任务（来自意图分析） ---
        if task_intent:
            try:
                from backend.task_scheduler import TaskScheduler  # type: ignore[import]
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
                    logger.info(f"[DingTalk] 自动创建任务: {created['task_name']}")
            except Exception as e:
                logger.error(f"[DingTalk] 自动创建任务失败: {e}")

        return ai_reply
