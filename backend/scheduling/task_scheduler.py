"""
task_scheduler.py — 定时任务调度器 (APScheduler + JSON 持久化)
"""
import os
import json
import uuid
from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from backend.integrations.notification_manager import NotificationManager  # type: ignore[import]

from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore[import]
from apscheduler.triggers.date import DateTrigger  # type: ignore[import]
from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import]
from apscheduler.triggers.cron import CronTrigger  # type: ignore[import]

from backend.config import TASKS_FILE, get_client, APP_SETTINGS  # type: ignore[import]


class TaskScheduler:
    """封装 APScheduler，提供任务 CRUD 和 JSON 持久化"""

    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.tasks: dict[str, dict] = {}  # task_id → task_data
        self.notification_manager: Optional["NotificationManager"] = None  # 多通道通知管理器
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

    # ========== 任务执行 ==========

    def _execute_task(self, task_id: str):
        """APScheduler 回调：调用大模型生成内容"""
        task = self.tasks.get(task_id)
        if not task:
            return

        prompt = task.get("action_prompt", "请生成一段提醒消息")
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            client = get_client(APP_SETTINGS["task_provider"])
            model = APP_SETTINGS["task_model"]
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是小鱼秘书，一个专业的AI助手。请根据用户的要求生成内容。"},
                    {"role": "user", "content": f"当前时间: {now_str}\n\n任务要求: {prompt}"},
                ],
                temperature=0.7,
                stream=False,
            )
            result = resp.choices[0].message.content.strip()
        except Exception as e:
            result = f"❌ 执行失败: {e}"

        # 更新任务状态
        task["last_run"] = now_str
        task["last_result"] = result[:500]  # type: ignore[index]
        self._save_tasks()
        print(f"[Scheduler] 任务 '{task['task_name']}' 执行完毕: {result[:80]}...")  # type: ignore[index]

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
                    "task_name": task["task_name"],
                    "result": result[:300],  # type: ignore[index]
                    "time": now_str,
                })
            except Exception as e:
                print(f"[Scheduler] 推送失败: {e}")

    # ========== 内部工具 ==========

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
