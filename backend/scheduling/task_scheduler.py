"""
task_scheduler.py — 定时任务调度器 (APScheduler + JSON 持久化)

支持 Plan-Act-Observe 多轮工具调用循环，让定时任务拥有与正常对话相同的能力
（联网搜索、图片生成等 MCP 工具）。
"""
import os
import re
import json
import uuid
from datetime import datetime
from typing import Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from backend.integrations.notification_manager import NotificationManager  # type: ignore[import]
    from backend.integrations.mcp_manager import MCPManager  # type: ignore[import]

from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore[import]
from apscheduler.triggers.date import DateTrigger  # type: ignore[import]
from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import]
from apscheduler.triggers.cron import CronTrigger  # type: ignore[import]

from backend.config import (  # type: ignore[import]
    TASKS_FILE, SESSION_DIR, SESSIONS_META_FILE,
    SYSTEM_PROMPT, MEMORY_FILE,
    get_client, get_model_caps, APP_SETTINGS,
)


def _build_task_system_prompt() -> str:
    """构建包含长期记忆的完整系统提示词（定时任务专用）"""
    memory_text = ""
    try:
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content and content != "# 长期记忆":
                memory_text = content
    except Exception:
        pass

    base = SYSTEM_PROMPT
    if memory_text:
        base += "\n\n--- 以下是关于用户的长期记忆，请在对话中参考 ---\n" + memory_text
    return base


class TaskScheduler:
    """封装 APScheduler，提供任务 CRUD 和 JSON 持久化"""

    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.tasks: dict[str, dict] = {}  # task_id → task_data
        self.notification_manager: Optional["NotificationManager"] = None  # 多通道通知管理器
        self.mcp_manager: Optional["MCPManager"] = None  # MCP 工具管理器（由 server.py 注入）
        self._load_tasks()

    # ========== 生命周期 ==========

    def start(self):
        """启动调度器，恢复所有运行中的任务"""
        if not self.scheduler.running:
            self.scheduler.start()
        # 恢复所有 status == "running" 的任务
        for task_id, task in self.tasks.items():
            if task["status"] == "running":
                self._register_job(task)
        print(f"[Scheduler] 调度器已启动，恢复了 {sum(1 for t in self.tasks.values() if t['status'] == 'running')} 个任务")

    def shutdown(self):
        """关闭调度器"""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        print("[Scheduler] 调度器已关闭")

    def register_system_job(self, job_id: str, func, trigger, **kwargs):
        """注册系统级定时任务（不进入用户任务列表）"""
        try:
            self.scheduler.add_job(
                func,
                trigger=trigger,
                id=job_id,
                replace_existing=True,
                **kwargs,
            )
            print(f"[Scheduler] 已注册系统任务: {job_id}")
        except Exception as e:
            print(f"[Scheduler] 注册系统任务 {job_id} 失败: {e}")

    # ========== CRUD ==========

    def create_task(
        self,
        task_name: str,
        trigger_type: str,
        trigger_args: dict,
        action_prompt: str,
    ) -> dict:
        """创建新定时任务"""
        task_id = "task_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4]  # type: ignore[index]

        task = {
            "task_id": task_id,
            "task_name": task_name,
            "trigger_type": trigger_type,    # "date" | "interval" | "cron"
            "trigger_args": trigger_args,
            "action_type": "ai_generate",
            "action_prompt": action_prompt,
            "status": "running",
            "created_at": datetime.now().isoformat(),
            "last_run": None,
            "last_result": None,
        }

        self.tasks[task_id] = task
        self._save_tasks()
        self._register_job(task)
        print(f"[Scheduler] 已创建任务: {task_name} ({trigger_type})")
        return task

    def pause_task(self, task_id: str) -> bool:
        """暂停任务"""
        if task_id not in self.tasks:
            return False
        self.tasks[task_id]["status"] = "paused"
        self._save_tasks()
        try:
            self.scheduler.pause_job(task_id)
        except Exception:
            pass
        print(f"[Scheduler] 已暂停任务: {task_id}")
        return True

    def resume_task(self, task_id: str) -> bool:
        """恢复任务"""
        if task_id not in self.tasks:
            return False
        task = self.tasks[task_id]
        task["status"] = "running"
        self._save_tasks()
        # 尝试恢复已有 job，如果不存在则重新注册
        try:
            self.scheduler.resume_job(task_id)
        except Exception:
            self._register_job(task)
        print(f"[Scheduler] 已恢复任务: {task_id}")
        return True

    def delete_task(self, task_id: str) -> bool:
        """删除任务"""
        if task_id not in self.tasks:
            return False
        try:
            self.scheduler.remove_job(task_id)
        except Exception:
            pass
        del self.tasks[task_id]  # type: ignore[misc]
        self._save_tasks()
        print(f"[Scheduler] 已删除任务: {task_id}")
        return True

    def list_tasks(self) -> list[dict]:
        """返回所有任务列表"""
        return list(self.tasks.values())

    # ========== 任务执行（Plan-Act-Observe 循环） ==========

    def _execute_task(self, task_id: str):
        """APScheduler 回调：使用完整的 Plan-Act-Observe 循环执行定时任务，
        支持 MCP 工具调用（联网搜索等）和多轮推理。"""
        task = self.tasks.get(task_id)
        if not task:
            return

        prompt = task.get("action_prompt", "请生成一段提醒消息")
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        task_name = task.get("task_name", "定时任务")

        print(f"[Scheduler] 开始执行任务 '{task_name}' (Plan-Act-Observe 模式)")

        try:
            result = self._run_task_with_tools(task_id, task_name, prompt, now_str)
        except Exception as e:
            result = f"❌ 执行失败: {e}"
            print(f"[Scheduler] 任务 '{task_name}' 执行异常: {e}")

        # 更新任务状态
        task["last_run"] = now_str
        task["last_result"] = result[:2000]  # type: ignore[index]
        self._save_tasks()
        print(f"[Scheduler] 任务 '{task_name}' 执行完毕: {result[:80]}...")  # type: ignore[index]

        # 一次性任务执行后自动标记完成
        if task["trigger_type"] == "date":
            task["status"] = "completed"
            self._save_tasks()

        # 推送结果到所有通知通道
        nm = self.notification_manager
        if nm is not None:
            try:
                nm.dispatch({
                    "type": "task_result",
                    "task_id": task_id,
                    "task_name": task_name,
                    "result": result,
                    "time": now_str,
                })
            except Exception as e:
                print(f"[Scheduler] 推送失败: {e}")

    def _run_task_with_tools(self, task_id: str, task_name: str, prompt: str, now_str: str) -> str:
        """使用 Plan-Act-Observe 循环执行任务，支持 MCP 工具调用。

        如果没有可用的 MCP 管理器，则降级为简单的单次 API 调用。
        """
        client = get_client(APP_SETTINGS["task_provider"])
        model = APP_SETTINGS["task_model"]
        model_caps = get_model_caps(APP_SETTINGS["task_provider"], model)
        max_loops = int(APP_SETTINGS.get("max_tool_loops", 6))

        # --- 加载 MCP 工具 ---
        mcp = self.mcp_manager
        mcp_tools: list[dict] = []
        if mcp is not None and APP_SETTINGS.get("enable_mcp_for_chat", False):
            try:
                mcp_tools = mcp.get_all_tools_for_loop("WEB_SEARCH")
                print(f"[Scheduler] 已加载 {len(mcp_tools)} 个 MCP 工具: "
                      f"{[t['function']['name'] for t in mcp_tools]}")
            except Exception as e:
                print(f"[Scheduler] 加载 MCP 工具失败 (降级为无工具模式): {e}")
                mcp_tools = []

        # --- 构建消息列表 ---
        system_prompt = _build_task_system_prompt()
        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
        ]

        # 工具使用指引（仅在有工具时注入）
        if mcp_tools:
            # 动态生成可用工具列表描述
            tool_desc_lines = []
            for i, t in enumerate(mcp_tools, 1):
                fn = t["function"]
                tool_desc_lines.append(f"{i}. {fn['name']}: {fn.get('description', '无描述')[:60]}")
            tool_list_str = "\n".join(tool_desc_lines)

            messages.append({
                "role": "system",
                "content": (
                    f"你正在执行一个定时任务，拥有以下工具能力：\n"
                    f"{tool_list_str}\n"
                    "\n【Plan→Act→Observe 工作流】\n"
                    "每次回复时，你必须遵循以下流程：\n"
                    "1. **Plan（规划）**: 先分析任务需求，决定需要哪些信息\n"
                    "2. **Act（执行）**: 调用工具获取信息\n"
                    "3. **Observe（观察）**: 分析工具返回结果，决定是否需要更多操作\n"
                    "\n重要：如果任务涉及新闻、天气、实时信息等，你**必须**先调用搜索工具获取真实数据，"
                    "**绝对不要**凭空编造任何新闻、数据或事件。\n"
                    f"你最多可以进行 {max_loops} 轮工具调用循环，请合理规划。"
                )
            })

        messages.append({
            "role": "user",
            "content": f"当前时间: {now_str}\n\n任务要求: {prompt}"
        })

        # --- 创建任务会话目录 ---
        session_id = f"task_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_{uuid.uuid4().hex[:6]}"
        session_dir = os.path.join(SESSION_DIR, session_id)
        os.makedirs(session_dir, exist_ok=True)
        chat_file = os.path.join(session_dir, "chat.md")

        # 初始化 chat.md
        with open(chat_file, "w", encoding="utf-8") as f:
            f.write(f"# 定时任务会话 — {task_name}\n\n")
            f.write(f"[系统] 定时任务触发于 {now_str}\n\n")
            f.write(f"User: [定时任务] {prompt}\n")

        # 注册到会话元数据
        self._register_task_session(session_id, task_name, now_str)

        # --- Plan-Act-Observe 循环 ---
        thinking_buf = ""
        reply_buf = ""
        tool_summary_parts: list[str] = []

        for loop_round in range(1, max_loops + 1):
            remaining = max_loops - loop_round + 1

            # 注入循环观察提示（第 2 轮起）
            if loop_round > 1:
                messages.append({
                    "role": "system",
                    "content": (
                        f"\n🔍 Observe — 第 {loop_round} 轮（剩余 {remaining} 步）\n"
                        f"上方的 tool 消息是上一轮工具的返回结果。请你：\n"
                        f"1. 分析从结果中获取了什么信息\n"
                        f"2. 决定下一步：调用更多工具 或 用获得的信息直接生成最终回复"
                    ),
                })

            # 调用模型（非流式）
            skip_temp = "reasoning" in model_caps or "fixed_temp" in model_caps
            api_kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "stream": False,
            }
            if not skip_temp:
                api_kwargs["temperature"] = 0.7
            if mcp_tools:
                api_kwargs["tools"] = mcp_tools

            try:
                resp = client.chat.completions.create(**api_kwargs)
            except Exception as e:
                # 某些模型不支持 tools 参数，降级为无工具模式重试
                if "400" in str(e) and "tools" in api_kwargs:
                    print(f"[Scheduler] 模型不支持 tools 参数，降级为无工具模式: {e}")
                    api_kwargs.pop("tools", None)
                    mcp_tools = []
                    resp = client.chat.completions.create(**api_kwargs)
                else:
                    raise

            choice = resp.choices[0]
            msg = choice.message

            # 提取思考内容（reasoning models）
            reasoning = (
                getattr(msg, "reasoning_content", None)
                or getattr(msg, "thinking", None)
                or getattr(msg, "thinking_content", None)
                or ""
            )
            if reasoning:
                thinking_buf += reasoning

            content = msg.content or ""

            # 检查是否有工具调用
            tool_calls = getattr(msg, "tool_calls", None)

            if tool_calls and mcp is not None:
                # 有工具调用 — 执行工具
                reply_buf += content  # 可能包含 Plan 分析文本

                # 记录 assistant 消息（含 tool_calls）
                assistant_msg: dict = {
                    "role": "assistant",
                    "content": content if content else None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            }
                        }
                        for tc in tool_calls
                    ]
                }
                messages.append(assistant_msg)

                # 逐个执行工具
                for tc in tool_calls:
                    t_name = tc.function.name
                    try:
                        t_args = json.loads(tc.function.arguments)
                    except Exception:
                        t_args = {}

                    friendly_name = {
                        "web_search": "Bing 联网搜索",
                        "jimeng_image_generation": "即梦 AI 图片生成",
                        "jimeng_video_generation": "即梦 AI 视频生成",
                    }.get(t_name, t_name.replace("_", " ").title())

                    print(f"[Scheduler] 第 {loop_round} 轮调用工具: {friendly_name}, 参数: {t_args}")

                    # 路由到正确的 MCP intent
                    _TOOL_TO_INTENT = {
                        "web_search": "WEB_SEARCH",
                        "jimeng_image_generation": "JIMENG",
                        "jimeng_video_generation": "JIMENG",
                    }
                    tool_intent = _TOOL_TO_INTENT.get(t_name, "WEB_SEARCH")

                    try:
                        t_result = mcp.execute_tool(
                            intent=tool_intent,
                            tool_name=t_name,
                            args=t_args,
                            session_id=session_id,
                            session_dir=session_dir,
                        )
                    except Exception as e:
                        t_result = f"工具执行失败: {e}"
                        print(f"[Scheduler] 工具 {t_name} 执行异常: {e}")

                    t_result_str = str(t_result)
                    tool_summary_parts.append(f"{friendly_name} → ✅")

                    # 记录到 chat.md
                    with open(chat_file, "a", encoding="utf-8") as f:
                        f.write(f"\n[工具调用] {friendly_name}: {json.dumps(t_args, ensure_ascii=False)}\n")
                        # 记录搜索结果的前 500 字符
                        f.write(f"[工具结果] {t_result_str[:500]}\n")

                    # 将工具结果注入消息上下文
                    # 清理 HTML 和内部指令，只保留有用信息
                    clean_result = re.sub(r'<[^>]+>', '', t_result_str)
                    clean_result = re.sub(r'【重要指令】.*', '', clean_result)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": t_name,
                        "content": clean_result.strip(),
                    })

                print(f"[Scheduler] Plan-Act-Observe 第 {loop_round} 轮完成，剩余 {remaining - 1} 步")
                continue  # 进入下一轮
            else:
                # 无工具调用 — 最终回复
                reply_buf = content
                break

        # --- 清理思考链标记 ---
        reply_buf = self._clean_reply(reply_buf)

        # --- 保存最终结果到 chat.md ---
        tool_summary = " | ".join(tool_summary_parts) if tool_summary_parts else ""
        with open(chat_file, "a", encoding="utf-8") as f:
            if thinking_buf:
                f.write(f"\n[思考]{thinking_buf}[/思考]\n")
            if tool_summary:
                f.write(f"[工具调用] {tool_summary}\n")
            f.write(f"\n小鱼: {reply_buf}\n")

        # 更新会话元数据
        self._finalize_task_session(session_id, task_name)

        print(f"[Scheduler] 任务会话已保存: {session_id}")
        return reply_buf

    # ========== 内部工具 ==========

    @staticmethod
    def _clean_reply(text: str) -> str:
        """清理 AI 回复中的思考链标记，只保留面向用户的最终内容。

        去除以下内部标记：
        - **Plan（规划）**: / **Observe（观察）**: 等阶段标记行
        - **最终回复**: 标记行
        - 💡 分析：... 行
        - 🔍 Observe — 第 N 轮 等循环提示
        """
        if not text:
            return text

        lines = text.split("\n")
        cleaned: list[str] = []
        skip_until_blank = False

        for line in lines:
            stripped = line.strip()

            # 跳过思考链阶段标记行及其后续内容（直到遇到空行）
            if re.match(r'\*{0,2}Plan[（(]规划[）)]', stripped, re.IGNORECASE):
                skip_until_blank = True
                continue
            if re.match(r'\*{0,2}Observe[（(]观察[）)]', stripped, re.IGNORECASE):
                skip_until_blank = True
                continue
            # 跳过 "最终回复" 标记行本身（但保留后续内容）
            if re.match(r'\*{0,2}最终回复\*{0,2}\s*[:：]?\s*$', stripped):
                skip_until_blank = False
                continue
            # 跳过 💡 分析行
            if stripped.startswith("💡 分析") or stripped.startswith("💡 分析"):
                continue
            # 跳过 🔍 Observe 循环提示
            if stripped.startswith("🔍 Observe"):
                continue

            # 遇到空行时停止跳过
            if skip_until_blank:
                if not stripped:
                    skip_until_blank = False
                continue

            cleaned.append(line)

        result = "\n".join(cleaned).strip()
        # 清理多余空行
        result = re.sub(r'\n{3,}', '\n\n', result)
        return result

    def _register_job(self, task: dict):
        """将任务注册到 APScheduler"""
        task_id = task["task_id"]
        trigger_type = task["trigger_type"]
        trigger_args = task["trigger_args"]

        # 先移除旧的同名 job (如果存在)
        try:
            self.scheduler.remove_job(task_id)
        except Exception:
            pass

        try:
            if trigger_type == "date":
                trigger = DateTrigger(run_date=trigger_args.get("run_date"))
            elif trigger_type == "interval":
                trigger = IntervalTrigger(**trigger_args)
            elif trigger_type == "cron":
                trigger = CronTrigger(**trigger_args)
            else:
                print(f"[Scheduler] 未知触发器类型: {trigger_type}")
                return

            self.scheduler.add_job(
                self._execute_task,
                trigger=trigger,
                args=[task_id],
                id=task_id,
                name=task["task_name"],
                replace_existing=True,
            )
        except Exception as e:
            print(f"[Scheduler] 注册任务 {task_id} 失败: {e}")

    def _load_tasks(self):
        """从 JSON 文件加载任务"""
        if os.path.exists(TASKS_FILE):
            try:
                with open(TASKS_FILE, "r", encoding="utf-8") as f:
                    tasks_list = json.load(f)
                self.tasks = {t["task_id"]: t for t in tasks_list}
            except Exception as e:
                print(f"[Scheduler] 加载任务文件失败: {e}")
                self.tasks = {}
        else:
            self.tasks = {}

    def _save_tasks(self):
        """保存任务到 JSON 文件"""
        try:
            with open(TASKS_FILE, "w", encoding="utf-8") as f:
                json.dump(list(self.tasks.values()), f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Scheduler] 保存任务文件失败: {e}")

    # ---------- 任务会话管理 ----------

    @staticmethod
    def _load_session_meta() -> dict:
        """加载会话元数据"""
        if os.path.exists(SESSIONS_META_FILE):
            try:
                with open(SESSIONS_META_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    @staticmethod
    def _save_session_meta(meta: dict):
        """保存会话元数据"""
        try:
            with open(SESSIONS_META_FILE, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Scheduler] 保存会话元数据失败: {e}")

    def _register_task_session(self, session_id: str, task_name: str, now_str: str):
        """将任务会话注册到会话元数据，使其在前端可见"""
        meta = self._load_session_meta()
        meta[session_id] = {
            "name": f"📋 {task_name} ({now_str[:10]})",
            "has_messages": True,
            "created_at": datetime.now().isoformat(),
            "last_active": datetime.now().isoformat(),
        }
        self._save_session_meta(meta)

    def _finalize_task_session(self, session_id: str, task_name: str):
        """任务执行完成后更新会话元数据"""
        meta = self._load_session_meta()
        if session_id in meta:
            meta[session_id]["last_active"] = datetime.now().isoformat()
            self._save_session_meta(meta)
