"""
schedule_manager.py — 智能日程管理器 (SQLite + 轮询提醒 + AI)

提醒机制: 不为每条日程注册独立 APScheduler job,
而是用 1 个系统级 cron job 每分钟扫描数据库,
找到需要提醒的日程统一推送, 不占用 task 系统槽位。
"""
import json
import uuid
import sqlite3
from datetime import datetime, timedelta, date
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from backend.task_scheduler import TaskScheduler
    from backend.notification_manager import NotificationManager

from backend.config import SCHEDULE_DB, get_client, APP_SETTINGS  # type: ignore[import]


# ========== 分类颜色映射 ==========
CATEGORY_COLORS = {
    "工作": "#4F86F7",
    "个人": "#34C759",
    "学习": "#AF52DE",
    "其他": "#FF9500",
    "会议": "#FF3B30",
    "健康": "#30D158",
}

DEFAULT_COLOR = "#4F86F7"


class ScheduleManager:
    """
    智能日程管理器：SQLite 持久化 + 轮询提醒 + AI 能力

    提醒策略: 仅注册 1 个系统级 cron job (每分钟一次),
    扫描 DB 中需要提醒的日程, 统一推送。
    """

    def __init__(self):
        self.db_path = SCHEDULE_DB
        self.task_scheduler: Optional["TaskScheduler"] = None
        self.notification_manager: Optional["NotificationManager"] = None
        self._notified_ids: set[str] = set()  # 已推送过的日程 id，避免重复
        self._init_db()

    # ========== 数据库初始化 ==========

    def _init_db(self):
        """创建 schedules 表"""
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schedules (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    all_day INTEGER DEFAULT 0,
                    category TEXT DEFAULT '其他',
                    color TEXT DEFAULT '#4F86F7',
                    location TEXT DEFAULT '',
                    rrule TEXT DEFAULT '',
                    reminder_minutes INTEGER DEFAULT 15,
                    status TEXT DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.commit()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        return dict(row)

    # ========== CRUD ==========

    def create(self, data: dict) -> dict:
        """
        创建日程。
        data 必须包含: title, start_time, end_time
        可选: description, all_day, category, color, location, rrule,
              reminder_minutes, status
        """
        schedule_id = "sch_" + datetime.now().strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:6]
        now_iso = datetime.now().isoformat()

        category = data.get("category", "其他")
        color = data.get("color") or CATEGORY_COLORS.get(category, DEFAULT_COLOR)

        schedule = {
            "id": schedule_id,
            "title": data["title"],
            "description": data.get("description", ""),
            "start_time": data["start_time"],
            "end_time": data["end_time"],
            "all_day": 1 if data.get("all_day") else 0,
            "category": category,
            "color": color,
            "location": data.get("location", ""),
            "rrule": data.get("rrule", ""),
            "reminder_minutes": data.get("reminder_minutes", 15),
            "status": data.get("status", "active"),
            "created_at": now_iso,
            "updated_at": now_iso,
        }

        with self._conn() as conn:
            conn.execute(
                """INSERT INTO schedules
                   (id, title, description, start_time, end_time, all_day,
                    category, color, location, rrule, reminder_minutes,
                    status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    schedule["id"], schedule["title"], schedule["description"],
                    schedule["start_time"], schedule["end_time"], schedule["all_day"],
                    schedule["category"], schedule["color"], schedule["location"],
                    schedule["rrule"], schedule["reminder_minutes"],
                    schedule["status"], schedule["created_at"], schedule["updated_at"],
                ),
            )
            conn.commit()

        print(f"[Schedule] ✅ 已创建日程: {schedule['title']} ({schedule['start_time']})")

        # 冲突检测
        conflicts = self.detect_conflicts(
            schedule["start_time"], schedule["end_time"], exclude_id=schedule_id
        )
        if conflicts:
            schedule["conflicts"] = conflicts
            print(f"[Schedule] ⚠️ 检测到 {len(conflicts)} 个时间冲突")

        return schedule

    def update(self, schedule_id: str, data: dict) -> Optional[dict]:
        """更新日程"""
        existing = self.get(schedule_id)
        if not existing:
            return None

        updatable = [
            "title", "description", "start_time", "end_time", "all_day",
            "category", "color", "location", "rrule", "reminder_minutes", "status",
        ]
        for key in updatable:
            if key in data:
                existing[key] = data[key]

        if "category" in data and "color" not in data:
            existing["color"] = CATEGORY_COLORS.get(data["category"], DEFAULT_COLOR)

        existing["updated_at"] = datetime.now().isoformat()
        if "all_day" in data:
            existing["all_day"] = 1 if data["all_day"] else 0

        with self._conn() as conn:
            conn.execute(
                """UPDATE schedules SET
                   title=?, description=?, start_time=?, end_time=?, all_day=?,
                   category=?, color=?, location=?, rrule=?, reminder_minutes=?,
                   status=?, updated_at=?
                   WHERE id=?""",
                (
                    existing["title"], existing["description"],
                    existing["start_time"], existing["end_time"], existing["all_day"],
                    existing["category"], existing["color"], existing["location"],
                    existing["rrule"], existing["reminder_minutes"],
                    existing["status"], existing["updated_at"],
                    schedule_id,
                ),
            )
            conn.commit()

        # 更新后清除已通知标记，允许重新触发
        self._notified_ids.discard(schedule_id)
        print(f"[Schedule] ✏️ 已更新日程: {existing['title']}")
        return existing

    def delete(self, schedule_id: str) -> bool:
        """删除日程"""
        with self._conn() as conn:
            cursor = conn.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
            conn.commit()
            if cursor.rowcount == 0:
                return False

        self._notified_ids.discard(schedule_id)
        print(f"[Schedule] 🗑️ 已删除日程: {schedule_id}")
        return True

    def get(self, schedule_id: str) -> Optional[dict]:
        """获取单条日程"""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone()
            return self._row_to_dict(row) if row else None

    def list_range(self, start: str, end: str) -> list[dict]:
        """
        查询时间范围内的日程（含重复日程展开）。
        start/end: ISO8601 日期字符串 (如 '2026-03-01' 或 '2026-03-01T00:00:00')
        """
        if len(start) <= 10:
            start += "T00:00:00"
        if len(end) <= 10:
            end += "T23:59:59"

        results: list[dict] = []

        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM schedules
                   WHERE rrule = '' AND status = 'active'
                   AND start_time <= ? AND end_time >= ?
                   ORDER BY start_time""",
                (end, start),
            ).fetchall()
            results.extend(self._row_to_dict(r) for r in rows)

            rrule_rows = conn.execute(
                "SELECT * FROM schedules WHERE rrule != '' AND status = 'active'"
            ).fetchall()

        for row in rrule_rows:
            expanded = self._expand_rrule(self._row_to_dict(row), start, end)
            results.extend(expanded)

        results.sort(key=lambda x: x["start_time"])
        return results

    def list_all(self) -> list[dict]:
        """列出所有日程"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM schedules ORDER BY start_time DESC"
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    # ========== 冲突检测 ==========

    def detect_conflicts(
        self, start_time: str, end_time: str, exclude_id: Optional[str] = None
    ) -> list[dict]:
        """检测与给定时间范围有重叠的日程"""
        with self._conn() as conn:
            if exclude_id:
                rows = conn.execute(
                    """SELECT * FROM schedules
                       WHERE status = 'active' AND id != ?
                       AND start_time < ? AND end_time > ?""",
                    (exclude_id, end_time, start_time),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM schedules
                       WHERE status = 'active'
                       AND start_time < ? AND end_time > ?""",
                    (end_time, start_time),
                ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ========== 轮询提醒 (1 个系统 job, 每分钟扫描) ==========

    def check_and_fire_reminders(self):
        """
        由 APScheduler 每分钟调用一次。
        扫描 DB, 找到「提醒时间 ∈ [now, now+1min]」的日程, 推送通知。
        仅占用 1 个 APScheduler job 槽位。
        """
        now = datetime.now()
        now_iso = now.isoformat()

        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM schedules WHERE status = 'active' AND start_time > ?",
                (now_iso,),
            ).fetchall()

        for row in rows:
            s = self._row_to_dict(row)
            sid = s["id"]
            if sid in self._notified_ids:
                continue

            try:
                start_dt = datetime.fromisoformat(s["start_time"])
                remind_at = start_dt - timedelta(minutes=s.get("reminder_minutes", 15))
                # 如果当前时间在 [remind_at, remind_at + 1min) 内 → 触发
                if remind_at <= now < remind_at + timedelta(minutes=1):
                    self._fire_reminder(s)
                    self._notified_ids.add(sid)
            except Exception as e:
                print(f"[Schedule] 提醒检查异常 ({sid}): {e}")

        # 清理过期已通知 id（开始时间已过 1 天的）
        yesterday = (now - timedelta(days=1)).isoformat()
        expired = set()
        for sid in self._notified_ids:
            sch = self.get(sid)
            if sch and sch["start_time"] < yesterday:
                expired.add(sid)
        self._notified_ids -= expired

    def _fire_reminder(self, schedule: dict):
        """推送单条日程提醒"""
        start_time = schedule["start_time"][11:16] if len(schedule["start_time"]) > 10 else ""
        msg = f"📅 日程提醒\n\n🏷 {schedule['title']}\n🕐 {start_time}"
        if schedule.get("location"):
            msg += f"\n📍 {schedule['location']}"
        if schedule.get("description"):
            msg += f"\n📝 {schedule['description']}"

        try:
            print(f"[Schedule] 🔔 触发提醒: {schedule['title']}")
        except UnicodeEncodeError:
            print(f"[Schedule] 触发提醒: {schedule['title']} (含有无法在终端显示的字符)")

        nm = self.notification_manager
        if nm is not None:
            try:
                nm.dispatch({
                    "type": "schedule_reminder",
                    "task_name": f"日程提醒: {schedule['title']}",
                    "result": msg,
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "schedule": schedule,
                })
            except Exception as e:
                print(f"[Schedule] 提醒推送失败: {e}")

    # ========== 每日摘要 ==========

    def generate_daily_briefing(self, target_date: Optional[str] = None) -> str:
        """AI 生成今日日程简报"""
        if not target_date:
            target_date = date.today().isoformat()

        day_start = f"{target_date}T00:00:00"
        day_end = f"{target_date}T23:59:59"
        schedules = self.list_range(day_start, day_end)

        if not schedules:
            return f"📅 {target_date} 没有安排日程，享受自由的一天吧！"

        items: list[str] = []
        for i, s in enumerate(schedules, 1):
            start = s["start_time"][11:16] if len(s["start_time"]) > 10 else "全天"
            end = s["end_time"][11:16] if len(s["end_time"]) > 10 else ""
            time_str = f"{start}-{end}" if end else start
            loc = f" 📍{s['location']}" if s.get("location") else ""
            items.append(f"{i}. [{time_str}] {s['title']}{loc}")

        schedule_text = "\n".join(items)

        try:
            client = get_client(APP_SETTINGS["summary_provider"])
            model = APP_SETTINGS["summary_model"]
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是小鱼秘书。请根据以下日程列表，生成一段简短亲切的每日简报。"
                            "语气温馨，适当加入 emoji。不超过 150 字。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"日期: {target_date}\n\n日程列表:\n{schedule_text}",
                    },
                ],
                temperature=0.7,
                stream=False,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            print(f"[Schedule] 生成日程简报失败: {e}")
            return f"📅 今日日程 ({target_date}):\n{schedule_text}"

    def send_daily_briefing(self):
        """每日简报推送（由 APScheduler 调用, 也仅 1 个 job）"""
        briefing = self.generate_daily_briefing()
        print(f"[Schedule] 📬 每日简报: {briefing[:80]}...")

        nm = self.notification_manager
        if nm is not None:
            try:
                nm.dispatch({
                    "type": "daily_briefing",
                    "task_name": "每日日程简报",
                    "result": briefing,
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
            except Exception as e:
                print(f"[Schedule] 简报推送失败: {e}")

    # ========== AI 自然语言解析 ==========

    def parse_natural_language(self, text: str) -> Optional[dict]:
        """
        使用 AI 将自然语言描述解析为日程 JSON。
        例如："明天下午2点到3点在会议室开项目会" → 结构化日程数据
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        weekday = weekday_names[datetime.now().weekday()]

        prompt = (
            "你是一个日程解析助手。请将用户的自然语言描述解析为结构化的日程 JSON。\n\n"
            f"当前时间: {now_str}（{weekday}）\n\n"
            "输出严格遵循以下 JSON 格式（不要有额外文字）：\n"
            "```json\n"
            "{\n"
            '  "title": "日程标题（简洁概括，10字以内）",\n'
            '  "description": "详细描述（可为空）",\n'
            '  "start_time": "YYYY-MM-DDTHH:MM:SS",\n'
            '  "end_time": "YYYY-MM-DDTHH:MM:SS",\n'
            '  "all_day": false,\n'
            '  "category": "工作|个人|学习|会议|健康|其他",\n'
            '  "location": "地点（可为空）",\n'
            '  "reminder_minutes": 15\n'
            "}\n"
            "```\n\n"
            "规则：\n"
            "- 如果用户没说结束时间，默认持续 1 小时\n"
            '- 如果用户说\'全天\'或没有具体时间，设 all_day 为 true，时间设为当天 00:00-23:59\n'
            "- 根据内容智能判断 category\n"
            "- reminder_minutes 默认 15，紧急事项设为 5\n"
        )

        try:
            client = get_client(APP_SETTINGS["judge_provider"])
            model = APP_SETTINGS["judge_model"]
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": text},
                ],
                temperature=0.1,
                stream=False,
            )
            answer = resp.choices[0].message.content.strip()

            # 提取 JSON
            if "```" in answer:
                parts = answer.split("```")
                for part in parts:
                    s = part.strip()
                    if s.startswith("json"):
                        s = s[4:].strip()
                    if s.startswith("{"):
                        answer = s
                        break

            data = json.loads(answer)
            required = ["title", "start_time", "end_time"]
            if all(k in data for k in required):
                print(f"[Schedule] 🧠 AI 解析成功: {data['title']}")
                return data
            else:
                print(f"[Schedule] AI 返回 JSON 缺少必要字段: {data}")
                return None

        except Exception as e:
            print(f"[Schedule] AI 解析失败: {e}")
            return None

    # ========== 重复规则展开 ==========

    def _expand_rrule(self, schedule: dict, range_start: str, range_end: str) -> list[dict]:
        """将重复日程展开为指定范围内的所有实例"""
        try:
            from dateutil.rrule import rrulestr  # type: ignore[import]

            base_start = datetime.fromisoformat(schedule["start_time"])
            base_end = datetime.fromisoformat(schedule["end_time"])
            duration = base_end - base_start

            rs = datetime.fromisoformat(range_start)
            re_ = datetime.fromisoformat(range_end)

            rule = rrulestr(schedule["rrule"], dtstart=base_start)
            occurrences = rule.between(rs - duration, re_, inc=True)

            results: list[dict] = []
            for occ_start in occurrences:
                occ_end = occ_start + duration
                instance = dict(schedule)
                instance["start_time"] = occ_start.isoformat()
                instance["end_time"] = occ_end.isoformat()
                instance["_recurring_instance"] = True
                results.append(instance)

            return results
        except ImportError:
            print("[Schedule] python-dateutil 未安装, 跳过重复日程展开")
            return []
        except Exception as e:
            print(f"[Schedule] 展开重复日程失败: {e}")
            return []
