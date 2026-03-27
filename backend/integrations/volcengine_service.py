"""
volcengine_service.py — 火山引擎即梦 (Jimeng) AI 图片/视频生成服务

使用官方 volcengine Python SDK 的 VisualService 进行 HMAC-SHA256 签名鉴权,
对接即梦系列 API（同步转异步模式: 提交任务 → 轮询查询 → 获取结果）。
"""
import json
import time
import asyncio

from backend.config import _SECRETS, _save_secrets  # type: ignore[import]


# ====== 支持的即梦 req_key 列表 ======
JIMENG_REQ_KEYS = {
    # 图片生成
    "jimeng_t2i_v30":  "即梦文生图 3.0",
    "jimeng_i2i_v30":  "即梦图生图 3.0",
    "jimeng_t2i_v40":  "即梦图片生成 4.0",
    # 视频生成
    "jimeng_t2v_v30_1080p":          "即梦文生视频 3.0 1080P",
    "jimeng_i2v_first_v30_1080":     "即梦图生视频 3.0 首帧",
    "jimeng_i2v_first_tail_v30_1080":"即梦图生视频 3.0 首尾帧",
    "jimeng_ti2v_v30_pro":           "即梦视频 3.0 Pro",
}

IMAGE_REQ_KEYS = {"jimeng_t2i_v30", "jimeng_i2i_v30", "jimeng_t2i_v40"}
VIDEO_REQ_KEYS = {"jimeng_t2v_v30_1080p", "jimeng_i2v_first_v30_1080",
                  "jimeng_i2v_first_tail_v30_1080", "jimeng_ti2v_v30_pro"}


def _get_volcengine_config() -> dict:
    """从 secrets.json 读取火山引擎配置"""
    return _SECRETS.get("volcengine", {})


class JimengService:
    """即梦 AI 服务客户端"""

    def __init__(self):
        self._visual_service = None

    def _init_client(self):
        """延迟初始化 VisualService 客户端"""
        if self._visual_service is not None:
            return True

        cfg = _get_volcengine_config()
        ak = cfg.get("access_key", "").strip()
        sk = cfg.get("secret_key", "").strip()

        if not ak or not sk:
            print("[Jimeng] ❌ 未配置火山引擎 AK/SK，无法调用即梦 API")
            return False

        try:
            from volcengine.visual.VisualService import VisualService  # type: ignore[import]
            self._visual_service = VisualService()
            self._visual_service.set_ak(ak)
            self._visual_service.set_sk(sk)
            print(f"[Jimeng] ✅ VisualService 初始化成功 (AK: {ak[:6]}...)")
            return True
        except ImportError:
            print("[Jimeng] ❌ 未安装 volcengine SDK: pip install volcengine")
            return False
        except Exception as e:
            print(f"[Jimeng] ❌ VisualService 初始化失败: {e}")
            return False

    def _submit_task(self, body: dict) -> dict:
        """提交异步任务 (CVSync2AsyncSubmitTask)"""
        if not self._init_client():
            return {"code": -1, "message": "VisualService 未初始化"}

        try:
            resp = self._visual_service.cv_sync2async_submit_task(body)
            print(f"[Jimeng] 提交任务响应: code={resp.get('code')}, task_id={resp.get('data', {}).get('task_id') if resp.get('data') else 'N/A'}")
            return resp
        except Exception as e:
            print(f"[Jimeng] 提交任务异常: {e}")
            return {"code": -1, "message": f"提交任务失败: {e}"}

    def _query_task(self, body: dict) -> dict:
        """查询异步任务结果 (CVSync2AsyncGetResult)"""
        if not self._init_client():
            return {"code": -1, "message": "VisualService 未初始化"}

        try:
            resp = self._visual_service.cv_sync2async_get_result(body)
            return resp
        except Exception as e:
            print(f"[Jimeng] 查询任务异常: {e}")
            return {"code": -1, "message": f"查询任务失败: {e}"}

    def _wait_for_result(self, req_key: str, task_id: str,
                         max_wait: int = 300, interval: int = 5) -> dict:
        """
        轮询等待任务完成。
        max_wait: 最大等待秒数 (图片约30s, 视频约2-5min)
        interval: 轮询间隔秒数
        """
        query_body = {
            "req_key": req_key,
            "task_id": task_id,
        }
        # 图片类请求返回 URL
        if req_key in IMAGE_REQ_KEYS:
            query_body["req_json"] = json.dumps({"return_url": True})

        elapsed = 0
        while elapsed < max_wait:
            time.sleep(interval)
            elapsed += interval

            resp = self._query_task(query_body)
            code = resp.get("code", -1)
            data = resp.get("data")

            if code != 10000:
                # 非成功状态码，可能是还在处理，也可能已失败
                msg = resp.get("message", "未知错误")
                if data and isinstance(data, dict):
                    status = data.get("status", "")
                    if status in ("in_queue", "generating"):
                        print(f"[Jimeng] 任务 {task_id} 状态: {status} ({elapsed}s)")
                        continue
                # 其他错误直接返回
                return resp

            if data and isinstance(data, dict):
                status = data.get("status", "")
                if status == "done":
                    print(f"[Jimeng] ✅ 任务 {task_id} 完成 ({elapsed}s)")
                    return resp
                elif status in ("in_queue", "generating"):
                    print(f"[Jimeng] 任务 {task_id} 状态: {status} ({elapsed}s)")
                    continue
                elif status in ("not_found", "expired"):
                    return {"code": -1, "message": f"任务 {status}"}
                else:
                    return resp
            else:
                continue

        return {"code": -1, "message": f"任务超时 ({max_wait}s)"}

    # ====== 高级 API (自动提交 + 等待) ======

    def generate_image(self, prompt: str, *,
                       req_key: str = "jimeng_t2i_v30",
                       image_urls: list[str] | None = None,
                       width: int | None = None,
                       height: int | None = None,
                       seed: int = -1,
                       scale: float | None = None,
                       use_pre_llm: bool | None = None) -> dict:
        """
        即梦图片生成 (文生图 / 图生图 / 4.0统一版)。
        返回: {"status": "ok"/"error", "image_urls": [...], ...}
        """
        body: dict = {
            "req_key": req_key,
            "prompt": prompt,
            "seed": seed,
        }

        if image_urls:
            body["image_urls"] = image_urls
        if width and height:
            body["width"] = width
            body["height"] = height
        if scale is not None:
            body["scale"] = scale
        if use_pre_llm is not None:
            body["use_pre_llm"] = use_pre_llm

        # 提交任务
        submit_resp = self._submit_task(body)
        if submit_resp.get("code") != 10000:
            return {
                "status": "error",
                "message": submit_resp.get("message", "提交失败"),
                "request_id": submit_resp.get("request_id", ""),
            }

        task_id = submit_resp["data"]["task_id"]
        print(f"[Jimeng] 图片任务已提交: {task_id} (req_key={req_key})")

        # 等待结果  (图片通常 20-60s)
        result = self._wait_for_result(req_key, task_id, max_wait=120, interval=3)

        if result.get("code") == 10000 and result.get("data", {}).get("status") == "done":
            data = result["data"]
            return {
                "status": "ok",
                "image_urls": data.get("image_urls", []),
                "binary_data_base64": data.get("binary_data_base64"),
                "task_id": task_id,
                "request_id": result.get("request_id", ""),
            }
        else:
            return {
                "status": "error",
                "message": result.get("message", "生成失败"),
                "request_id": result.get("request_id", ""),
            }

    def generate_video(self, prompt: str, *,
                       req_key: str = "jimeng_t2v_v30_1080p",
                       image_urls: list[str] | None = None,
                       frames: int = 121,
                       aspect_ratio: str = "16:9",
                       seed: int = -1) -> dict:
        """
        即梦视频生成 (文生视频 / 图生视频 / Pro)。
        返回: {"status": "ok"/"error", "video_url": "...", ...}
        """
        body: dict = {
            "req_key": req_key,
            "prompt": prompt,
            "seed": seed,
            "frames": frames,
        }

        if image_urls:
            body["image_urls"] = image_urls
        # aspect_ratio 只在文生视频或 Pro 下有效
        if req_key in ("jimeng_t2v_v30_1080p", "jimeng_ti2v_v30_pro"):
            body["aspect_ratio"] = aspect_ratio

        # 提交任务
        submit_resp = self._submit_task(body)
        if submit_resp.get("code") != 10000:
            return {
                "status": "error",
                "message": submit_resp.get("message", "提交失败"),
                "request_id": submit_resp.get("request_id", ""),
            }

        task_id = submit_resp["data"]["task_id"]
        print(f"[Jimeng] 视频任务已提交: {task_id} (req_key={req_key})")

        # 等待结果 (视频通常 1-5min)
        result = self._wait_for_result(req_key, task_id, max_wait=360, interval=5)

        if result.get("code") == 10000 and result.get("data", {}).get("status") == "done":
            data = result["data"]
            return {
                "status": "ok",
                "video_url": data.get("video_url", ""),
                "task_id": task_id,
                "request_id": result.get("request_id", ""),
            }
        else:
            return {
                "status": "error",
                "message": result.get("message", "生成失败"),
                "request_id": result.get("request_id", ""),
            }


# 单例
jimeng_service = JimengService()
