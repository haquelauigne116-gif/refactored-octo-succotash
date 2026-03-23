"""
session_manager.py — 会话生命周期管理 (创建、切换、清理、文件解析)

每个会话对应一个独立文件夹:
  data/session/{sid}/
    chat.md        — 对话记录 (供 AI 上下文使用)
    events.json    — 特殊事件 (文件卡片、任务卡片等，不参与 AI 上下文)
    assets/        — 预留多模态资源 (图片等)
"""
import os
import json
import uuid
import shutil
from datetime import datetime

from backend.config import (  # type: ignore[import]
    SESSION_DIR, SESSIONS_META_FILE, SYSTEM_PROMPT, MEMORY_FILE,
    get_client, APP_SETTINGS,
)


def _build_system_prompt() -> str:
    """构建包含长期记忆的完整系统提示词"""
    memory_text = ""
    try:
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
            # 只有当 memory.md 有实质内容时才注入
            if content and content != "# 长期记忆":
                memory_text = content
    except Exception:
        pass

    if memory_text:
        return SYSTEM_PROMPT + "\n\n--- 以下是关于用户的长期记忆，请在对话中参考 ---\n" + memory_text
    return SYSTEM_PROMPT


class SessionManager:
    """封装所有会话状态与操作，消除散落的 global 变量"""

    def __init__(self):
        self.session_id: str = ""
        self.file_path: str = ""
        self.events_path: str = ""
        self.session_dir: str = ""
        self.messages: list[dict] = []
        self.is_first_message: bool = True

        # 迁移旧格式 & 清理所有空会话
        self._migrate_legacy_sessions()
        self._cleanup_all_empty()

        # 启动时创建一个待用会话
        self.create_new()

    # ---------- 元数据 I/O ----------

    @staticmethod
    def _load_meta() -> dict:
        if os.path.exists(SESSIONS_META_FILE):
            with open(SESSIONS_META_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    @staticmethod
    def _save_meta(meta: dict):
        with open(SESSIONS_META_FILE, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    # ---------- 会话 CRUD ----------

    def create_new(self):
        """创建新会话（懒加载：只写元数据，不创建文件）"""
        # 先清理上一个空会话
        self._cleanup_empty(self.session_id)

        sid: str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + "_" + uuid.uuid4().hex[:6]  # type: ignore[index]
        self.session_id = sid
        self.session_dir = os.path.join(SESSION_DIR, sid)
        self.file_path = os.path.join(self.session_dir, "chat.md")
        self.events_path = os.path.join(self.session_dir, "events.json")
        self.messages = [{"role": "system", "content": _build_system_prompt()}]
        self.is_first_message = True

        meta = self._load_meta()
        meta[sid] = {
            "name": "",
            "has_messages": False,
            "created_at": datetime.now().isoformat(),
            "last_active": datetime.now().isoformat(),
        }
        self._save_meta(meta)

    def initialize_file(self):
        """第一条消息时，才真正创建会话文件夹和 .md 文件"""
        os.makedirs(self.session_dir, exist_ok=True)
        if not os.path.exists(self.file_path):
            with open(self.file_path, "w", encoding="utf-8") as f:
                f.write(f"# 会话记录\n\nSystem: {SYSTEM_PROMPT}\n\n")

    def switch_to(self, session_id: str) -> dict | None:
        """切换到已有会话，返回 { messages, events } 或 None（失败时）"""
        self._cleanup_empty(self.session_id)

        sid = session_id.replace(".md", "")
        target_dir = os.path.join(SESSION_DIR, sid)
        target_chat = os.path.join(target_dir, "chat.md")

        if not os.path.exists(target_chat):
            return None

        self.session_id = sid
        self.session_dir = target_dir
        self.file_path = target_chat
        self.events_path = os.path.join(target_dir, "events.json")
        self.is_first_message = False
        self.messages = [{"role": "system", "content": _build_system_prompt()}]

        # 多行状态机解析
        self._parse_session_file(target_chat)

        return {
            "messages": self.messages[1:],  # type: ignore[index]
            "events": self._load_events(),
        }

    def list_all(self) -> list[dict]:
        """返回所有有消息的会话列表（按最后活跃倒序）"""
        meta = self._load_meta()
        sessions = [
            {
                "id": sid,
                "filename": sid,  # 现在是文件夹名而非文件名
                "name": info.get("name", sid),
                "active": sid == self.session_id,
                "last_active": info.get("last_active", ""),
            }
            for sid, info in meta.items()
            if info.get("has_messages", False)
        ]
        sessions.sort(key=lambda s: s["last_active"], reverse=True)
        return sessions

    # ---------- 消息写入与元数据更新 ----------

    def append_user_message(self, text: str):
        """追加用户消息到内存和文件"""
        self.messages.append({"role": "user", "content": text})
        with open(self.file_path, "a", encoding="utf-8") as f:
            f.write(f"User: {text}\n")

    def append_ai_message(self, text: str, thinking: str = ""):
        """追加 AI 回复到内存和文件（含可选的思考内容）"""
        msg: dict = {"role": "assistant", "content": text}
        if thinking:
            msg["thinking"] = thinking
        self.messages.append(msg)
        with open(self.file_path, "a", encoding="utf-8") as f:
            if thinking:
                f.write(f"[思考]{thinking}[/思考]\n")
            f.write(f"小鱼: {text}\n\n")

    def pop_last_message(self):
        """回滚最后一条消息（用于 AI 调用失败时）"""
        if len(self.messages) > 1:
            self.messages.pop()

    def mark_first_message_done(self):
        """标记第一条消息已发送：生成标题、更新元数据"""
        self.is_first_message = False
        meta = self._load_meta()
        if self.session_id in meta:
            meta[self.session_id]["has_messages"] = True
            meta[self.session_id]["last_active"] = datetime.now().isoformat()
            meta[self.session_id]["name"] = self._generate_name()
            self._save_meta(meta)

    def update_activity(self):
        """更新活跃时间，每 10 轮重命名"""
        meta = self._load_meta()
        if self.session_id not in meta:
            return
        meta[self.session_id]["last_active"] = datetime.now().isoformat()

        msg_count = len(self.messages)
        if (msg_count - 1) % 20 == 0:
            new_name = self._generate_name()
            meta[self.session_id]["name"] = new_name
            print(f"会话 {self.session_id} 达到 {msg_count} 条消息，重命名为: {new_name}")

        self._save_meta(meta)

    # ---------- 事件持久化 (文件卡片、任务卡片等) ----------

    def append_event(self, event_type: str, data: dict, after_msg_index: int | None = None):
        """追加事件到 events.json（不参与 AI 上下文）"""
        events = self._load_events()
        # after_msg_index 表示该事件出现在第几条消息之后 (不含 system)
        if after_msg_index is None:
            after_msg_index = len(self.messages) - 1
        events.append({
            "type": event_type,
            "after_msg_index": after_msg_index,
            "data": data,
            "time": datetime.now().isoformat(),
        })
        self._save_events(events)

    def _load_events(self) -> list[dict]:
        """加载会话事件列表"""
        if os.path.exists(self.events_path):
            try:
                with open(self.events_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def _save_events(self, events: list[dict]):
        """保存事件列表到 events.json"""
        os.makedirs(self.session_dir, exist_ok=True)
        with open(self.events_path, "w", encoding="utf-8") as f:
            json.dump(events, f, ensure_ascii=False, indent=2)

    # ---------- 私有方法 ----------

    def _cleanup_empty(self, sid: str):
        """清理没有消息的空会话"""
        if not sid:
            return
        meta = self._load_meta()
        if sid in meta and not meta[sid].get("has_messages", False):
            # 清理会话文件夹（如果存在）
            session_dir = os.path.join(SESSION_DIR, sid)
            if os.path.isdir(session_dir):
                shutil.rmtree(session_dir, ignore_errors=True)
            # 兼容：清理旧格式的散落 .md 文件
            old_file = os.path.join(SESSION_DIR, f"{sid}.md")
            if os.path.exists(old_file):
                os.remove(old_file)
            del meta[sid]  # type: ignore[misc]
            self._save_meta(meta)

    def _cleanup_all_empty(self):
        """启动时批量清理所有空会话，解决历史残留问题"""
        meta = self._load_meta()
        to_delete = [
            sid for sid, info in meta.items()
            if not info.get("has_messages", False)
        ]
        if not to_delete:
            return
        for sid in to_delete:
            session_dir = os.path.join(SESSION_DIR, sid)
            if os.path.isdir(session_dir):
                shutil.rmtree(session_dir, ignore_errors=True)
            old_file = os.path.join(SESSION_DIR, f"{sid}.md")
            if os.path.exists(old_file):
                os.remove(old_file)
            del meta[sid]  # type: ignore[misc]
        self._save_meta(meta)
        print(f"[SessionManager] 启动清理: 删除了 {len(to_delete)} 个空会话")

    def _migrate_legacy_sessions(self):
        """将旧格式散落的 .md 文件迁移到 {sid}/chat.md 文件夹结构"""
        migrated: int = 0
        for fname in os.listdir(SESSION_DIR):
            if not fname.endswith(".md") or fname.startswith("_"):
                continue
            old_path = os.path.join(SESSION_DIR, fname)
            if not os.path.isfile(old_path):
                continue
            sid = fname.replace(".md", "")
            new_dir = os.path.join(SESSION_DIR, sid)
            new_path = os.path.join(new_dir, "chat.md")
            if os.path.exists(new_path):
                # 已迁移过，删除旧文件
                os.remove(old_path)
                migrated += 1  # type: ignore[operator]
                continue
            os.makedirs(new_dir, exist_ok=True)
            shutil.move(old_path, new_path)
            migrated += 1  # type: ignore[operator]
        if migrated > 0:
            print(f"[SessionManager] 迁移了 {migrated} 个旧会话文件到文件夹结构")

    def _parse_session_file(self, filepath: str):
        """多行状态机解析 .md 会话文件（含 [思考] 解析）"""
        current_role = None
        current_content: list[str] = []
        pending_thinking = ""
        in_thinking = False
        thinking_buf: list[str] = []
        user_prefix = "User: "
        ai_prefix = "小鱼: "

        def flush():
            nonlocal current_role, current_content, pending_thinking
            if current_role and current_content:
                text = "\n".join(current_content).strip()
                if text:
                    msg: dict = {"role": current_role, "content": text}
                    if current_role == "assistant" and pending_thinking:
                        msg["thinking"] = pending_thinking
                    self.messages.append(msg)
            current_role = None
            current_content = []
            pending_thinking = ""

        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                raw = line.rstrip("\n").rstrip("\r")

                # 处理思考标签
                if raw.startswith("[思考]"):
                    in_thinking = True
                    # 可能在同一行有内容
                    rest = raw[len("[思考]"):]  # type: ignore[index]
                    if rest.endswith("[/思考]"):
                        pending_thinking = rest[:-len("[/思考]")]
                        in_thinking = False
                    else:
                        thinking_buf = [rest] if rest else []
                    continue
                if in_thinking:
                    if raw.endswith("[/思考]"):
                        thinking_buf.append(raw[:-len("[/思考]")])  # type: ignore[index]
                        pending_thinking = "\n".join(thinking_buf)
                        in_thinking = False
                        thinking_buf = []
                    else:
                        thinking_buf.append(raw)  # type: ignore[attr-defined]
                    continue

                if raw.startswith(user_prefix):
                    flush()
                    current_role = "user"
                    current_content = [raw[len(user_prefix):]]  # type: ignore[index]
                elif raw.startswith(ai_prefix):
                    flush()
                    current_role = "assistant"
                    current_content = [raw[len(ai_prefix):]]  # type: ignore[index]
                elif raw.startswith("[系统]"):
                    flush()
                elif current_role:
                    current_content.append(raw)
        flush()

    def _generate_name(self) -> str:
        """用 AI 生成 ≤10 字的会话标题"""
        try:
            client = get_client(APP_SETTINGS["summary_provider"])
            model = APP_SETTINGS["summary_model"]
            history = "\n".join(
                f"{m['role']}: {m['content']}" for m in self.messages[-6:]  # type: ignore[index]
            )
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是一个标题助手。请总结这段对话的主题，字数在10字以内，只返回简体中文，不要标点。"},
                    {"role": "user", "content": f"对话历史：\n{history}"},
                ],
                temperature=0.3,
                stream=False,
            )
            return resp.choices[0].message.content.strip()[:10]
        except Exception as e:
            print(f"生成标题失败: {e}")
            return (
                self.messages[1]["content"][:10]
                if len(self.messages) > 1
                else "新会话"
            )
