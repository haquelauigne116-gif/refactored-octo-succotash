"""
napcat_watchdog.py — NapCat 守护进程

功能：
1. WebSocket 心跳监控 — 追踪来自 NapCat 的最后一次心跳/消息，
   超时后判定连接断开并发出告警。
2. 登录状态监控   — 定期调用 OneBot 11 /get_login_info 接口，
   检测 QQ 是否仍然在线；若登录失效则告警 + 尝试重启 NapCat。
3. NapCat 进程守护 — 检测 NapCat 进程是否存活，崩溃时自动重启。

用法（由 server.py lifespan 中启动）：
    watchdog = NapCatWatchdog(napcat_http_url="http://127.0.0.1:3000")
    watchdog.start()
    # ...
    watchdog.stop()
"""

import logging
import subprocess
import threading
import time
from datetime import datetime
from typing import Callable, Optional

import requests

logger = logging.getLogger(__name__)


class NapCatWatchdog:
    """NapCat 连接 & 登录状态守护"""

    def __init__(
        self,
        napcat_http_url: str = "http://127.0.0.1:3000",
        napcat_token: str = "",
        # ---------- 心跳 ----------
        heartbeat_timeout: int = 90,        # 心跳超时秒数（NapCat 默认 30s 一次心跳）
        # ---------- 登录检查 ----------
        login_check_interval: int = 60,     # 登录状态轮询间隔（秒）
        # ---------- 进程守护 ----------
        napcat_cmd: Optional[str] = None,   # NapCat 启动命令（为 None 则不尝试自动重启）
        max_restart_attempts: int = 3,      # 单轮最大重启尝试次数
        restart_cooldown: int = 300,        # 重启失败后的冷却期（秒）
        # ---------- 告警回调 ----------
        on_alert: Optional[Callable[[str, str], None]] = None,
    ):
        self.napcat_http_url = napcat_http_url.rstrip("/")
        self.napcat_token = napcat_token

        # 心跳
        self.heartbeat_timeout = heartbeat_timeout
        self._last_heartbeat: float = time.time()
        self._ws_connected: bool = False

        # 登录
        self.login_check_interval = login_check_interval
        self._login_ok: bool = False
        self._consecutive_send_failures: int = 0  # 连续发送超时计数
        self._login_info: dict = {}

        # 进程守护
        self.napcat_cmd = napcat_cmd
        self.max_restart_attempts = max_restart_attempts
        self.restart_cooldown = restart_cooldown
        self._restart_count: int = 0
        self._last_restart_time: float = 0

        # 通知回调: on_alert(level, message)  level = "warning" | "error" | "info"
        self._on_alert = on_alert

        # 控制
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # 统计
        self._stats = {
            "total_disconnects": 0,
            "total_login_failures": 0,
            "total_restarts": 0,
            "last_disconnect_time": None,
            "last_login_failure_time": None,
            "started_at": None,
        }

    # ============================================================
    # 公开接口 — 由 server.py 的 /ws/qq 端点回调
    # ============================================================

    def report_heartbeat(self):
        """NapCat 发来心跳或任意消息时调用"""
        with self._lock:
            self._last_heartbeat = time.time()
            if not self._ws_connected:
                self._ws_connected = True
                self._restart_count = 0  # 连接恢复，复位重启计数
                self._alert("info", "✅ NapCat WebSocket 连接已建立")

    def report_ws_disconnect(self):
        """WebSocket 断开时调用"""
        with self._lock:
            self._ws_connected = False
            self._stats["total_disconnects"] += 1
            self._stats["last_disconnect_time"] = datetime.now().isoformat()
        self._alert("warning", "⚠️ NapCat WebSocket 连接已断开，等待自动重连…")

    def report_ws_connect(self):
        """WebSocket 连接成功时调用"""
        with self._lock:
            self._ws_connected = True
            self._last_heartbeat = time.time()
            self._restart_count = 0
        self._alert("info", "✅ NapCat WebSocket 已连接")

    # ============================================================
    # 生命周期
    # ============================================================

    def start(self):
        """启动守护线程"""
        if self._running:
            return
        self._running = True
        self._stats["started_at"] = datetime.now().isoformat()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="napcat-watchdog")
        self._thread.start()
        logger.info("[Watchdog] NapCat 守护进程已启动")

    def stop(self):
        """停止守护线程"""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("[Watchdog] NapCat 守护进程已停止")

    def get_status(self) -> dict:
        """返回当前状态摘要（可由 API 暴露给前端）"""
        with self._lock:
            now = time.time()
            hb_age = now - self._last_heartbeat
            return {
                "ws_connected": self._ws_connected,
                "login_ok": self._login_ok,
                "login_info": self._login_info,
                "last_heartbeat_ago_sec": round(hb_age, 1),
                "restart_count": self._restart_count,
                "stats": dict(self._stats),
            }

    # ============================================================
    # 主循环
    # ============================================================

    def _run_loop(self):
        """后台主循环 — 两种检查交替执行"""
        logger.info("[Watchdog] 守护循环已开始运行")

        # 给 NapCat 一些启动时间
        time.sleep(10)

        tick = 0
        while self._running:
            try:
                # ---- 心跳超时检测（每 10s 检查一次） ----
                self._check_heartbeat_timeout()

                # ---- 登录状态检查（每 login_check_interval 秒） ----
                if tick % max(self.login_check_interval // 10, 1) == 0:
                    self._check_login_status()

            except Exception as e:
                logger.error(f"[Watchdog] 守护循环异常: {e}")

            # 睡眠 10 秒
            for _ in range(10):
                if not self._running:
                    return
                time.sleep(1)
            tick += 1

    # ============================================================
    # 心跳超时
    # ============================================================

    def _check_heartbeat_timeout(self):
        """检测心跳是否超时"""
        with self._lock:
            if not self._ws_connected:
                return  # 已知断开，不重复告警
            elapsed = time.time() - self._last_heartbeat

        if elapsed > self.heartbeat_timeout:
            with self._lock:
                self._ws_connected = False
                self._stats["total_disconnects"] += 1
                self._stats["last_disconnect_time"] = datetime.now().isoformat()

            self._alert(
                "error",
                f"🔴 NapCat 心跳超时 ({int(elapsed)}s > {self.heartbeat_timeout}s)，"
                f"判定 WebSocket 已断开"
            )
            # 尝试重启
            self._try_restart("心跳超时")

    # ============================================================
    # 登录状态
    # ============================================================

    def _check_login_status(self):
        """调用 OneBot /get_login_info + /get_status 双重检查 QQ 是否真正在线

        NapCat 在被 KickedOffLine 后，/get_login_info 仍可能返回缓存的
        旧数据（retcode=0），但实际已无法发送消息（僵尸状态）。
        因此额外调用 /get_status 验证 online 字段。
        """
        headers = {"Content-Type": "application/json"}
        if self.napcat_token:
            headers["Authorization"] = f"Bearer {self.napcat_token}"

        try:
            # 第一步：/get_login_info 基础检查
            resp = requests.get(
                f"{self.napcat_http_url}/get_login_info",
                headers=headers, timeout=10,
            )
            result = resp.json()

            if not (result.get("status") == "ok" or result.get("retcode") == 0):
                self._handle_login_failure(f"API 返回异常: {result}")
                return

            data = result.get("data", {})
            user_id = data.get("user_id", "")
            nickname = data.get("nickname", "")

            # 第二步：/get_status 检测真实在线状态（防僵尸）
            truly_online = True
            try:
                status_resp = requests.get(
                    f"{self.napcat_http_url}/get_status",
                    headers=headers, timeout=10,
                )
                status_result = status_resp.json()
                status_data = status_result.get("data", {})
                # NapCat/go-cqhttp: online=true 代表 QQ 真正在线
                if status_data.get("online") is False:
                    truly_online = False
                # 部分实现用 good 字段
                if status_data.get("good") is False:
                    truly_online = False
            except Exception:
                pass  # /get_status 不可用时退回到仅 login_info 判断

            if not truly_online:
                self._handle_login_failure(
                    f"QQ 进程存活但登录已失效（僵尸状态）— "
                    f"user_id={user_id}, online=false"
                )
                return

            # 一切正常
            with self._lock:
                was_offline = not self._login_ok
                self._login_ok = True
                self._login_info = {"user_id": user_id, "nickname": nickname}
                self._consecutive_send_failures = 0

            if was_offline:
                self._alert("info", f"✅ QQ 登录正常 — {nickname} ({user_id})")

            logger.debug(f"[Watchdog] QQ 登录正常: {nickname} ({user_id})")

        except requests.ConnectionError:
            self._handle_login_failure("无法连接到 NapCat HTTP 服务 — 进程可能未运行")
        except requests.Timeout:
            self._handle_login_failure("NapCat HTTP 请求超时")
        except Exception as e:
            self._handle_login_failure(f"检查登录状态异常: {e}")

    def _handle_login_failure(self, reason: str):
        """处理登录失败"""
        with self._lock:
            was_online = self._login_ok
            self._login_ok = False
            self._stats["total_login_failures"] += 1
            self._stats["last_login_failure_time"] = datetime.now().isoformat()

        if was_online:
            self._alert("error", f"🔴 QQ 登录失效 — {reason}")
        else:
            logger.warning(f"[Watchdog] QQ 登录异常: {reason}")

        self._try_restart(reason)

    # ============================================================
    # 自动重启 NapCat
    # ============================================================

    def _try_restart(self, reason: str):
        """尝试重启 NapCat 进程"""
        if not self.napcat_cmd:
            logger.info("[Watchdog] 未配置 napcat_cmd，跳过自动重启")
            return

        now = time.time()
        with self._lock:
            # 冷却期内不重启
            if now - self._last_restart_time < self.restart_cooldown and self._restart_count >= self.max_restart_attempts:
                logger.warning(
                    f"[Watchdog] 重启冷却中 ({self.max_restart_attempts} 次失败后 "
                    f"等待 {self.restart_cooldown}s)"
                )
                return

            # 超过冷却期，重置计数
            if now - self._last_restart_time >= self.restart_cooldown:
                self._restart_count = 0

            if self._restart_count >= self.max_restart_attempts:
                return

            self._restart_count += 1
            self._last_restart_time = now
            self._stats["total_restarts"] += 1
            attempt = self._restart_count

        self._alert(
            "warning",
            f"🔄 正在尝试重启 NapCat (第 {attempt}/{self.max_restart_attempts} 次) — 原因: {reason}"
        )

        try:
            # 先尝试杀掉旧进程
            self._kill_napcat()
            time.sleep(3)

            # 启动新进程 — 需要从 exe 所在目录运行（加载 DLL），
            # 且保留控制台窗口以便用户看到二维码。
            import os
            napcat_dir = os.path.dirname(os.path.abspath(self.napcat_cmd))
            napcat_exe = os.path.basename(self.napcat_cmd)
            # 用 cmd /k 包装，与 start.bat 中的启动方式一致
            launch_cmd = f'start "NapCat" cmd /k "chcp 65001 >nul & cd /d {napcat_dir} & {napcat_exe}"'
            subprocess.Popen(launch_cmd, shell=True)
            self._alert("info", f"✅ NapCat 进程已重新启动 (第 {attempt} 次)")
            logger.info(f"[Watchdog] NapCat 已重启 (第 {attempt} 次)")

            # 等待 NapCat 启动后重新连接
            time.sleep(20)

        except Exception as e:
            self._alert("error", f"❌ NapCat 重启失败: {e}")
            logger.error(f"[Watchdog] 重启失败: {e}")

    @staticmethod
    def _kill_napcat():
        """尝试结束 NapCat 相关进程（Windows / Linux）"""
        import sys
        try:
            if sys.platform == "win32":
                # NapCat Shell 启动器
                subprocess.run(
                    'taskkill /F /IM NapCatWinBootMain.exe 2>nul',
                    shell=True, capture_output=True,
                )
                # NapCat 也可能以 napcat.exe 出现
                subprocess.run(
                    'taskkill /F /IM napcat.exe 2>nul',
                    shell=True, capture_output=True,
                )
                # 杀掉 QQ 进程（NapCat 嵌入在 QQ 中运行）
                subprocess.run(
                    'taskkill /F /IM QQ.exe 2>nul',
                    shell=True, capture_output=True,
                )
            else:
                subprocess.run(
                    'pkill -f napcat 2>/dev/null || true',
                    shell=True, capture_output=True,
                )
        except Exception as e:
            logger.warning(f"[Watchdog] 终止旧进程失败: {e}")

    # ============================================================
    # 告警
    # ============================================================

    def _alert(self, level: str, message: str):
        """发出告警（日志 + 回调）"""
        now_str = datetime.now().strftime("%H:%M:%S")
        full_msg = f"[{now_str}] {message}"

        if level == "error":
            logger.error(f"[Watchdog] {message}")
        elif level == "warning":
            logger.warning(f"[Watchdog] {message}")
        else:
            logger.info(f"[Watchdog] {message}")

        # 调用外部回调（例如推送到 WebSocket / 钉钉）
        if self._on_alert:
            try:
                self._on_alert(level, full_msg)
            except Exception as e:
                logger.error(f"[Watchdog] 告警回调失败: {e}")
