"""
minio_manager.py — MinIO 文件管理 (上传/搜索/删除/AI自动标签/元信息提取)

本地 JSON 索引 + MinIO 存储，搜索/列表从索引读取，零 API 调用。
"""
import os
import io
import json
import logging
import threading
from datetime import datetime, timedelta

from minio import Minio  # type: ignore[import]
from minio.error import S3Error  # type: ignore[import]

from backend.config import (  # type: ignore[import]
    MINIO_INDEX_FILE, load_minio_config,
    get_client, APP_SETTINGS,
)

logger = logging.getLogger(__name__)


# MinIO 配置从 secrets.json 统一加载
# 使用 config.load_minio_config()


# ========== 文件元信息提取 ==========

def _extract_audio_metadata(file_path: str, filename: str) -> dict:
    """从音频文件提取元信息 (title, artists, album, genre, duration)"""
    try:
        import mutagen  # type: ignore[import]
        from mutagen.easyid3 import EasyID3  # type: ignore[import]
        from mutagen.mp3 import MP3  # type: ignore[import]
        from mutagen.flac import FLAC  # type: ignore[import]
        from mutagen.oggvorbis import OggVorbis  # type: ignore[import]
        from mutagen.mp4 import MP4  # type: ignore[import]
        from mutagen.id3 import ID3NoHeaderError  # type: ignore[import]
    except ImportError:
        logger.warning("[Meta] mutagen 未安装，跳过音频元信息提取")
        return {}

    result: dict = {}
    ext = os.path.splitext(filename)[1].lower()

    try:
        audio = mutagen.File(file_path)
        if audio is None:
            return {}

        # 统一提取常见字段
        def _get(keys: list[str]) -> list[str]:
            for k in keys:
                val = audio.get(k)
                if val:
                    if isinstance(val, list):
                        return [str(v) for v in val]
                    return [str(val)]
            return []

        # MP3 ID3 tags
        if ext == ".mp3":
            title = _get(["TIT2", "title", "\xa9nam"])
            artists = _get(["TPE1", "artist", "\xa9ART"])
            album = _get(["TALB", "album", "\xa9alb"])
            genre = _get(["TCON", "genre", "\xa9gen"])
        # MP4/M4A
        elif ext in (".m4a", ".mp4", ".aac"):
            title = _get(["\xa9nam", "title"])
            artists = _get(["\xa9ART", "aART", "artist"])
            album = _get(["\xa9alb", "album"])
            genre = _get(["\xa9gen", "genre"])
        # FLAC / OGG
        else:
            title = _get(["title"])
            artists = _get(["artist", "albumartist", "performer"])
            album = _get(["album"])
            genre = _get(["genre"])

        if title:
            result["title"] = title[0]
        if artists:
            # 处理 "Artist1/Artist2" 或 "Artist1;Artist2" 格式
            split_artists: list[str] = []
            for a in artists:
                for sep in ["/", ";", "、", "&", ","]:
                    if sep in a:
                        split_artists.extend(p.strip() for p in a.split(sep) if p.strip())
                        break
                else:
                    split_artists.append(a.strip())
            result["artists"] = split_artists
        if album:
            result["album"] = album[0]
        if genre:
            result["genre"] = genre[0]
        if hasattr(audio, "info") and hasattr(audio.info, "length"):
            result["duration"] = round(audio.info.length, 1)

        # (已移除同步提取歌词逻辑，改为按需下载提取)

    except Exception as e:
        logger.warning(f"[Meta] 音频元信息提取失败 ({filename}): {e}")

    return result


def _extract_image_metadata(file_path: str, filename: str) -> dict:
    """从图片提取 EXIF 元信息 (GPS 坐标 → 位置标签, 相机型号, 拍摄时间)"""
    try:
        from PIL import Image  # type: ignore[import]
        from PIL.ExifTags import TAGS, GPSTAGS  # type: ignore[import]
    except ImportError:
        logger.warning("[Meta] Pillow 未安装，跳过图片 EXIF 提取")
        return {}

    result: dict = {}
    try:
        img = Image.open(file_path)
        exif_data = img._getexif()
        if not exif_data:
            return {}

        exif: dict = {}
        for tag_id, value in exif_data.items():
            tag_name = TAGS.get(tag_id, tag_id)
            exif[tag_name] = value

        # 相机型号
        if "Model" in exif:
            result["camera"] = str(exif["Model"]).strip()

        # 拍摄时间
        if "DateTimeOriginal" in exif:
            result["taken_at"] = str(exif["DateTimeOriginal"])
        elif "DateTime" in exif:
            result["taken_at"] = str(exif["DateTime"])

        # GPS 坐标
        gps_info = exif.get("GPSInfo")
        if gps_info:
            gps: dict = {}
            for key, val in gps_info.items():
                gps_tag = GPSTAGS.get(key, key)
                gps[gps_tag] = val

            def _dms_to_decimal(dms: tuple, ref: str) -> float:
                d, m, s = float(dms[0]), float(dms[1]), float(dms[2])
                decimal = d + m / 60.0 + s / 3600.0
                if ref in ("S", "W"):
                    decimal = -decimal
                return float(f"{decimal:.6f}")

            if "GPSLatitude" in gps and "GPSLatitudeRef" in gps:
                lat = _dms_to_decimal(gps["GPSLatitude"], gps["GPSLatitudeRef"])
                result["latitude"] = lat
            if "GPSLongitude" in gps and "GPSLongitudeRef" in gps:
                lon = _dms_to_decimal(gps["GPSLongitude"], gps["GPSLongitudeRef"])
                result["longitude"] = lon

            # 尝试反向地理编码（用免费 API）
            if "latitude" in result and "longitude" in result:
                location = _reverse_geocode(result["latitude"], result["longitude"])
                if location:
                    result["location"] = location

    except Exception as e:
        logger.warning(f"[Meta] 图片 EXIF 提取失败 ({filename}): {e}")

    return result


def _extract_video_metadata(file_path: str, filename: str) -> dict:
    """从视频提取基本信息（目前只记录文件扩展名作为类型）"""
    # 视频元信息提取需要 ffprobe 等重量级工具，暂用简单方式
    result: dict = {}
    ext = os.path.splitext(filename)[1].lower()
    if ext in (".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm"):
        result["type"] = "视频"
        result["format"] = ext.lstrip(".")
    return result


def _reverse_geocode(lat: float, lon: float) -> str:
    """简易反向地理编码，返回位置描述"""
    try:
        import urllib.request
        url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json&zoom=10&accept-language=zh"
        req = urllib.request.Request(url, headers={"User-Agent": "XiaoYu-AI/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return data.get("display_name", "")[:50]
    except Exception as e:
        logger.warning(f"[Meta] 反向地理编码失败: {e}")
        return f"{lat:.4f},{lon:.4f}"


def extract_file_metadata(file_path: str, filename: str, content_type: str) -> dict:
    """根据文件类型分发元信息提取"""
    ct = (content_type or "").lower()
    ext = os.path.splitext(filename)[1].lower()

    # 音频
    if ct.startswith("audio/") or ext in (".mp3", ".wav", ".flac", ".ogg", ".aac", ".m4a", ".wma"):
        return {"file_type": "audio", **_extract_audio_metadata(file_path, filename)}

    # 图片
    if ct.startswith("image/") or ext in (".jpg", ".jpeg", ".png", ".tiff", ".heic", ".heif"):
        return {"file_type": "image", **_extract_image_metadata(file_path, filename)}

    # 视频
    if ct.startswith("video/") or ext in (".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm"):
        return {"file_type": "video", **_extract_video_metadata(file_path, filename)}

    return {"file_type": "other"}


def _metadata_to_tags(meta: dict) -> list[str]:
    """将提取的元信息转换为标签列表"""
    tags: list[str] = []
    if meta.get("title"):
        tags.append(meta["title"])
    if meta.get("artists"):
        tags.extend(meta["artists"])  # 每个创作者独立标签
    if meta.get("album"):
        tags.append(meta["album"])
    if meta.get("genre"):
        tags.append(meta["genre"])
    if meta.get("camera"):
        tags.append(meta["camera"])
    if meta.get("location"):
        # 取位置中有意义的部分（如城市名）
        loc_parts: list[str] = [p.strip() for p in str(meta["location"]).split(",") if p.strip()]
        # 取最后 2-3 个有意义的地名（通常是 区, 市, 省）
        for i in range(min(3, len(loc_parts))):
            if len(loc_parts[i]) <= 15:
                tags.append(str(loc_parts[i]))
    if meta.get("taken_at"):
        taken: str = str(meta['taken_at'])
        tags.append("拍摄于" + "".join(taken[i] for i in range(min(10, len(taken)))))
    return tags


class MinIOManager:
    """MinIO 文件管理器 — MinIO 只存文件，所有信息存本地索引"""

    def __init__(self):
        cfg = load_minio_config()
        self.bucket = cfg.get("bucket", "ai-assistant")
        self.enabled = bool(cfg.get("endpoint"))
        self._index: dict[str, dict] = {}  # object_name → file_entry
        self._index_lock = threading.Lock()

        if self.enabled:
            try:
                self.client = Minio(
                    endpoint=cfg["endpoint"],
                    access_key=cfg.get("access_key", ""),
                    secret_key=cfg.get("secret_key", ""),
                    secure=cfg.get("secure", False),
                )
                if not self.client.bucket_exists(self.bucket):
                    self.client.make_bucket(self.bucket)
                    logger.info(f"[MinIO] 创建 bucket: {self.bucket}")
                logger.info(f"[MinIO] 连接成功: {cfg['endpoint']}/{self.bucket}")
                # 加载本地索引 + 后台同步
                self._load_index()
                threading.Thread(target=self._sync_index, daemon=True).start()
            except Exception as e:
                logger.error(f"[MinIO] 连接失败: {e}")
                self.enabled = False
                self.client = None  # type: ignore[assignment]
        else:
            self.client = None  # type: ignore[assignment]
            logger.warning("[MinIO] 未配置，文件管理功能不可用")

    # ========== 本地索引管理 ==========

    def _load_index(self) -> None:
        """从磁盘加载索引文件"""
        try:
            if os.path.exists(MINIO_INDEX_FILE):
                with open(MINIO_INDEX_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                with self._index_lock:
                    self._index = data if isinstance(data, dict) else {}
                # Debug: 检查 cover_art 是否存在
                for k, v in self._index.items():
                    fm = v.get("file_meta", {})
                    has_cover = "cover_art" in fm if isinstance(fm, dict) else False
                    logger.info(f"[MinIO] 索引加载 {k}: cover_art={has_cover}")
                logger.info(f"[MinIO] 索引加载成功: {len(self._index)} 个文件")
            else:
                logger.info("[MinIO] 索引文件不存在，将从 MinIO 同步")
        except Exception as e:
            logger.warning(f"[MinIO] 索引加载失败: {e}")

    def _save_index(self) -> None:
        """将索引持久化到磁盘"""
        try:
            with self._index_lock:
                snapshot = dict(self._index)
            with open(MINIO_INDEX_FILE, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[MinIO] 索引保存失败: {e}")

    def _index_put(self, entry: dict) -> None:
        """写入/更新一条索引记录并持久化"""
        obj_name = entry["object_name"]
        with self._index_lock:
            self._index[obj_name] = entry
        self._save_index()

    def _index_remove(self, object_name: str) -> None:
        """删除一条索引记录并持久化"""
        with self._index_lock:
            self._index.pop(object_name, None)
        self._save_index()

    def _index_update_tags(self, object_name: str, tags: list[str],
                           file_meta: dict | None = None) -> None:
        """更新索引中某个文件的标签和元信息"""
        with self._index_lock:
            if object_name in self._index:
                self._index[object_name]["tags"] = tags
                self._index[object_name]["tagging_status"] = "done"
                if file_meta:
                    self._index[object_name]["file_meta"] = file_meta
        self._save_index()

    def _next_id(self) -> int:
        """生成下一个自增编号"""
        with self._index_lock:
            if not self._index:
                return 1
            # 从现有 object_name 中提取最大编号
            max_id = 0
            for key in self._index:
                try:
                    num = int(os.path.splitext(key)[0])
                    if num > max_id:
                        max_id = num
                except (ValueError, IndexError):
                    pass
            return int(max_id) + 1

    def _sync_index(self) -> None:
        """后台与 MinIO 做同步校验（启动时执行一次，只核对文件是否存在）"""
        try:
            remote_names: set[str] = set()
            for obj in self.client.list_objects(self.bucket):
                remote_names.add(obj.object_name)
                if obj.object_name not in self._index:
                    # MinIO 有但索引没有 → 补入基本条目（信息已丢失）
                    stat = self.client.stat_object(self.bucket, obj.object_name)
                    with self._index_lock:
                        self._index[obj.object_name] = {
                            "object_name": obj.object_name,
                            "original_name": obj.object_name,
                            "description": "",
                            "tags": [],
                            "content_type": stat.content_type or "",
                            "size": stat.size or 0,
                            "uploaded_at": "",
                            "tagging_status": "unknown",
                        }

            # 索引有但 MinIO 没有 → 从索引删除
            with self._index_lock:
                stale = [k for k in self._index if k not in remote_names]
                for k in stale:
                    self._index.pop(k, None)

            if stale or any(n not in self._index for n in remote_names):
                self._save_index()

            logger.info(f"[MinIO] 索引同步完成: {len(self._index)} 个文件")
        except Exception as e:
            logger.error(f"[MinIO] 索引同步失败: {e}")

    # ========== AI 打标签（含元信息增强） ==========

    def _generate_tags(self, filename: str, description: str,
                       file_meta: dict | None = None) -> list[str]:
        # 先从文件元信息提取硬标签
        meta_tags = _metadata_to_tags(file_meta) if file_meta else []

        try:
            provider = APP_SETTINGS.get("summary_provider", "deepseek")
            model = APP_SETTINGS.get("summary_model", "deepseek-chat")
            ai_client = get_client(provider)

            # 构建元信息上下文
            meta_context = ""
            if file_meta:
                parts: list[str] = []
                if file_meta.get("title"):
                    parts.append("歌曲名: " + str(file_meta['title']))
                if file_meta.get("artists"):
                    parts.append("创作者: " + ', '.join(str(a) for a in file_meta['artists']))
                if file_meta.get("album"):
                    parts.append("专辑: " + str(file_meta['album']))
                if file_meta.get("genre"):
                    parts.append("风格: " + str(file_meta['genre']))
                if file_meta.get("camera"):
                    parts.append("设备: " + str(file_meta['camera']))
                if file_meta.get("location"):
                    parts.append("拍摄地点: " + str(file_meta['location']))
                if file_meta.get("taken_at"):
                    parts.append("拍摄时间: " + str(file_meta['taken_at']))
                if parts:
                    meta_context = "\n文件元信息：" + "；".join(parts)

            resp = ai_client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是一个文件标签生成助手。根据文件名、用户描述和文件自带元信息，生成 8-15 个简短标签。\n"
                            "标签应覆盖：文件类型、内容主题、创作者、风格等维度。\n"
                            "如果有创作者/艺术家信息，将每个简体中文创作者名作为独立标签。\n"
                            "如果有地点信息，提取城市/地区名作为标签。\n"
                            "直接输出标签，用逗号分隔，不要编号和解释。\n"
                            "示例：周杰伦, 青花瓷, 中国风, R&B, 流行音乐, 华语音乐, 高品质, 慢歌"
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"文件名：{filename}\n描述：{description or '无'}{meta_context}",
                    },
                ],
                temperature=0.3,
                stream=False,
            )
            raw: str = resp.choices[0].message.content.strip()
            ai_tags = [t.strip() for t in raw.replace("，", ",").split(",") if t.strip()]

            # 合并：元信息标签优先 + AI 标签去重补充
            seen = set()
            merged: list[str] = []
            for t in meta_tags + ai_tags:
                if t.lower() not in seen:
                    seen.add(t.lower())
                    merged.append(t)
            return merged[:25]  # type: ignore[return-value]

        except Exception as e:
            logger.error(f"[MinIO] AI 标签生成失败: {e}")
            if meta_tags:
                return list(meta_tags)[0:25]  # type: ignore[return-value]
            ext = os.path.splitext(filename)[1].lstrip(".")
            return [ext] if ext else ["文件"]

    # ========== 核心操作 ==========

    def upload_fast(self, filename: str, file_data: bytes, content_type: str,
                    description: str = "") -> dict:
        """快速上传：写入 MinIO（编号命名），索引记录信息，立即返回"""
        if not self.enabled:
            raise RuntimeError("MinIO 未配置")

        # 用自增编号 + 原始扩展名作为 object_name
        ext = os.path.splitext(filename)[1]  # 如 .mp3, .jpg
        file_id = self._next_id()
        object_name = f"{file_id:05d}{ext}"
        uploaded_at = datetime.now().isoformat()

        # MinIO 只存文件二进制
        self.client.put_object(
            self.bucket,
            object_name,
            io.BytesIO(file_data),
            length=len(file_data),
            content_type=content_type,
        )

        logger.info(f"[MinIO] 快速上传成功: {object_name} ← {filename}")
        entry = {
            "object_name": object_name,
            "original_name": filename,
            "description": description,
            "tags": [],
            "content_type": content_type,
            "size": len(file_data),
            "uploaded_at": uploaded_at,
            "tagging_status": "pending",
        }
        # 写入本地索引
        self._index_put(entry)
        return entry

    def process_tags(self, object_name: str, filename: str,
                     content_type: str, description: str = "") -> dict:
        """后台处理：提取元信息 → AI 打标签 → 更新本地索引（不写 MinIO）"""
        
        # 1. 将文件从 MinIO 下载到本地临时文件
        temp_dir = os.path.join("data", "temp")
        os.makedirs(temp_dir, exist_ok=True)
        import hashlib
        safe_name = hashlib.md5(object_name.encode("utf-8")).hexdigest() + os.path.splitext(filename)[1]
        temp_path = os.path.join(temp_dir, "tmp_meta_" + safe_name)
        
        try:
            self.client.fget_object(self.bucket, object_name, temp_path)
            # 2. 提取文件自带元信息
            file_meta = extract_file_metadata(temp_path, filename, content_type)
            logger.info(f"[MinIO] 元信息提取完成: {filename} → {file_meta}")
            
            # 3. AI 打标签
            tags = self._generate_tags(filename, description, file_meta)
            
            # 4. 只更新本地索引（不再写 MinIO metadata/tags）
            self._index_update_tags(object_name, tags, file_meta)
            
            logger.info(f"[MinIO] 标签处理完成: {object_name} 标签: {tags}")
            return {
                "object_name": object_name,
                "original_name": filename,
                "tags": tags,
                "file_meta": file_meta,
            }
        except Exception as e:
            logger.error(f"[MinIO] 标签处理失败: {object_name} ({e})")
            return {
                "object_name": object_name,
                "original_name": filename,
                "tags": [],
                "file_meta": {},
            }
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
        return {
            "object_name": object_name,
            "original_name": filename,
            "tags": [],
            "file_meta": {},
        }

    def upload(self, filename: str, file_data: bytes, content_type: str,
               description: str = "") -> dict:
        """同步上传（兼容旧调用）：快速上传 + 同步处理标签"""
        entry = self.upload_fast(filename, file_data, content_type, description)
        result = self.process_tags(
            entry["object_name"], filename, content_type, description
        )
        entry["tags"] = result["tags"]
        entry["file_meta"] = result["file_meta"]
        entry["tagging_status"] = "done"
        return entry

    def list_files(self) -> list[dict]:
        """列出所有文件（从本地索引读取，零 API 调用）"""
        if not self.enabled:
            return []
        with self._index_lock:
            return list(self._index.values())

    def search(self, query: str) -> list[dict]:
        """搜索文件（从本地索引，毫秒级响应）"""
        all_files = self.list_files()
        if not query.strip():
            return all_files

        q = query.lower()
        return [
            f for f in all_files
            if q in f["original_name"].lower()
            or q in f.get("description", "").lower()
            or any(q in tag.lower() for tag in f.get("tags", []))
        ]

    def ai_search(self, prompt: str) -> dict:
        """AI 智能检索文件：根据用户自然语言描述，挑选最佳匹配文件"""
        if not self.enabled:
            raise RuntimeError("MinIO 未配置")

        all_files = self.list_files()
        if not all_files:
            return {"status": "error", "message": "MinIO 中暂无文件", "files": []}

        # 构建文件列表摘要给 AI
        files_info = "\n".join(
            f"{i+1}.文件名:{f['original_name']}|类型:{f.get('content_type', '未知')}|标签:{','.join(f.get('tags', []))}|描述:{f.get('description', '无')}"
            for i, f in enumerate(all_files) if i < 150
        )

        try:
            provider = APP_SETTINGS.get("summary_provider", "deepseek")
            model = APP_SETTINGS.get("summary_model", "deepseek-chat")
            ai_client = get_client(provider)

            resp = ai_client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是一个智能文件检索助手。用户会输入一段自然语言描述，你需要从提供的文件列表中，"
                            "挑选出最符合用户描述的文件。\n\n"
                            "请严格按以下 JSON 格式返回，不要输出其他任何内容：\n"
                            '{"reason": "简短分析匹配原因", "selected": [1, 3]}\n\n'
                            "其中 selected 是你挑选的文件序号列表（按匹配度排序，最多挑选 5 个）。\n"
                            "如果没有符合的文件，selected 可以为空数组 []。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"用户需求：{prompt}\n\n可用文件列表：\n{files_info}",
                    },
                ],
                temperature=0.3,
                stream=False,
            )
            raw = resp.choices[0].message.content.strip()

            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip()

            result = json.loads(raw)
            selected_indices = result.get("selected", [])

            files = []
            for idx in selected_indices:
                real_idx = idx - 1
                if 0 <= real_idx < len(all_files):
                    f = dict(all_files[real_idx])
                    f["download_url"] = self.get_download_url(f["object_name"], force_download=True, filename=f["original_name"])
                    files.append(f)

            return {
                "status": "ok",
                "reason": result.get("reason", ""),
                "files": files,
            }

        except Exception as e:
            logger.error(f"[MinIO] AI 文件检索失败: {e}")
            return {"status": "error", "message": f"AI 检索失败: {e}", "files": []}

    def delete(self, object_name: str) -> bool:
        """删除文件（同时清理索引）"""
        if not self.enabled:
            return False
        try:
            self.client.remove_object(self.bucket, object_name)
            self._index_remove(object_name)
            logger.info(f"[MinIO] 已删除: {object_name}")
            return True
        except S3Error as e:
            logger.error(f"[MinIO] 删除失败: {e}")
            return False

    def get_download_url(self, object_name: str, force_download: bool = False, filename: str | None = None) -> str:
        """生成预签名下载 URL（有效期 1 小时）"""
        if not self.enabled:
            return ""
        try:
            from typing import Any
            kwargs: dict[str, Any] = {"expires": timedelta(hours=1)}
            if force_download:
                import urllib.parse
                fname = filename if filename else object_name.split('/')[-1]
                encoded_name = urllib.parse.quote(fname)
                kwargs["response_headers"] = {
                    "response-content-disposition": f"attachment; filename*=UTF-8''{encoded_name}"
                }
            return self.client.presigned_get_object(self.bucket, object_name, **kwargs)
        except Exception as e:
            logger.error(f"[MinIO] 生成下载链接失败: {e}")
            return ""

    # ========== 音乐功能 ==========

    _AUDIO_TYPES = {"audio/mpeg", "audio/mp3", "audio/wav", "audio/flac",
                    "audio/ogg", "audio/aac", "audio/x-m4a", "audio/mp4"}

    def _is_audio(self, content_type: str, filename: str) -> bool:
        """判断是否为音频文件"""
        if content_type and content_type.lower() in self._AUDIO_TYPES:
            return True
        ext = os.path.splitext(filename)[1].lower()
        return ext in {".mp3", ".wav", ".flac", ".ogg", ".aac", ".m4a", ".wma"}

    def search_audio(self) -> list[dict]:
        """列出所有音频文件"""
        all_files = self.list_files()
        return [f for f in all_files if self._is_audio(
            f.get("content_type", ""), f.get("original_name", "")
        )]

    def get_cover_art_file(self, object_name: str) -> str | None:
        """获取本地缓存的封面路径，如果不存在则从 MinIO 下载并提取"""
        if not self.enabled:
            return None

        # 缓存目录
        covers_dir = os.path.join("data", "covers")
        temp_dir = os.path.join("data", "temp")
        os.makedirs(covers_dir, exist_ok=True)
        os.makedirs(temp_dir, exist_ok=True)

        # 安全的文件名 (利用 hashlib 防止特殊字符)
        import hashlib
        safe_name = hashlib.md5(object_name.encode("utf-8")).hexdigest() + ".jpg"
        cover_path = os.path.join(covers_dir, safe_name)

        if os.path.exists(cover_path):
            return cover_path

        # 如果没有缓存，判断是否是音频，如果是则下载并提取
        if not self._is_audio("", object_name):
            return None

        temp_path = os.path.join(temp_dir, "tmp_" + safe_name)
        try:
            self.client.fget_object(self.bucket, object_name, temp_path)
            
            # 使用 mutagen 提取图片
            import mutagen  # type: ignore[import]
            audio = mutagen.File(temp_path)
            cover_data = None

            if audio is not None:
                ext = os.path.splitext(object_name)[1].lower()
                if ext == ".mp3":
                    for key in (audio.tags or {}):
                        if str(key).startswith("APIC"):
                            cover_data = audio.tags[key].data
                            break
                elif ext in (".m4a", ".mp4", ".aac"):
                    covr = audio.get("covr")
                    if covr and len(covr) > 0:
                        cover_data = bytes(covr[0])
                else:
                    if hasattr(audio, 'pictures') and audio.pictures:
                        cover_data = audio.pictures[0].data

            # 存入缓存
            if cover_data:
                with open(cover_path, "wb") as f:
                    f.write(cover_data)
                return cover_path
            else:
                return None
        except Exception as e:
            logger.warning(f"[CoverArt] 提取封面失败 {object_name}: {e}")
            return None
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def get_lyrics(self, object_name: str) -> str:
        """获取本地缓存的歌词，如果不存在则从 MinIO 下载并提取"""
        if not self.enabled:
            return ""

        lyrics_dir = os.path.join("data", "lyrics")
        temp_dir = os.path.join("data", "temp")
        os.makedirs(lyrics_dir, exist_ok=True)
        os.makedirs(temp_dir, exist_ok=True)

        import hashlib
        safe_name = hashlib.md5(object_name.encode("utf-8")).hexdigest() + ".lrc"
        lyrics_path = os.path.join(lyrics_dir, safe_name)

        if os.path.exists(lyrics_path):
            with open(lyrics_path, "r", encoding="utf-8") as f:
                return f.read()

        if not self._is_audio("", object_name):
            return ""

        temp_path = os.path.join(temp_dir, "tmp_lyric_" + safe_name)
        lyrics_text = ""
        try:
            self.client.fget_object(self.bucket, object_name, temp_path)
            
            import mutagen  # type: ignore[import]
            audio = mutagen.File(temp_path)
            if audio is not None:
                ext = os.path.splitext(object_name)[1].lower()

                if ext == ".mp3":
                    for key in (audio.tags or {}):
                        if str(key).startswith("USLT"):
                            lyrics_text = str(audio.tags[key])
                            break
                elif ext in (".m4a", ".mp4", ".aac"):
                    lyr = audio.get("\xa9lyr")
                    if lyr:
                        lyrics_text = str(lyr[0]) if isinstance(lyr, list) else str(lyr)
                else:
                    lyr = audio.get("lyrics") or audio.get("LYRICS")
                    if lyr:
                        lyrics_text = str(lyr[0]) if isinstance(lyr, list) else str(lyr)

            if lyrics_text and lyrics_text.strip():
                with open(lyrics_path, "w", encoding="utf-8") as f:
                    f.write(lyrics_text.strip())
                return lyrics_text.strip()
            return ""
        except Exception as e:
            logger.warning(f"[Lyrics] 提取歌词失败 {object_name}: {e}")
            return ""
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        return ""

    def generate_playlist(self, prompt: str) -> dict:
        """AI 智能生成歌单：从已有音频中挑选并排序"""
        if not self.enabled:
            raise RuntimeError("MinIO 未配置")

        audio_files = self.search_audio()
        if not audio_files:
            return {
                "playlist_name": "空歌单",
                "description": "MinIO 中暂无音频文件，请先上传一些音乐。",
                "songs": [],
            }

        # 构建文件列表摘要给 AI
        songs_info = "\n".join(
            f"{i+1}. 文件名: {f['original_name']} | 标签: {', '.join(f.get('tags', []))} | 描述: {f.get('description', '无')}"
            for i, f in enumerate(audio_files)
        )

        try:
            provider = APP_SETTINGS.get("summary_provider", "deepseek")
            model = APP_SETTINGS.get("summary_model", "deepseek-chat")
            ai_client = get_client(provider)

            resp = ai_client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是一个音乐歌单生成助手。用户会告诉你一个心情、场景或偏好，"
                            "你需要从下面的音频文件列表中挑选适合的歌曲并排序。\n\n"
                            "请严格按以下 JSON 格式返回，不要输出其他任何内容：\n"
                            '{"playlist_name": "歌单名称(简短有创意)", '
                            '"description": "一句话描述这个歌单的氛围", '
                            '"selected": [1, 3, 5]}\n\n'
                            "其中 selected 是你挑选的歌曲编号列表（按推荐顺序）。\n"
                            "如果所有歌曲都适合，可以全部选上。如果都不太匹配也没关系，"
                            "尽量挑最接近的，至少选 1 首。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"用户需求：{prompt}\n\n可用歌曲列表：\n{songs_info}",
                    },
                ],
                temperature=0.5,
                stream=False,
            )
            raw = resp.choices[0].message.content.strip()

            # 解析 AI 返回的 JSON
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip()

            result = json.loads(raw)
            selected_indices = result.get("selected", [])

            # 构建歌单
            songs = []
            for idx in selected_indices:
                real_idx = idx - 1
                if 0 <= real_idx < len(audio_files):
                    f = audio_files[real_idx]
                    f["download_url"] = self.get_download_url(f["object_name"])
                    songs.append(f)

            return {
                "playlist_name": result.get("playlist_name", "AI 歌单"),
                "description": result.get("description", "为你精心挑选的歌曲"),
                "songs": songs,
            }

        except Exception as e:
            logger.error(f"[MinIO] AI 歌单生成失败: {e}")
            for f in audio_files:
                f["download_url"] = self.get_download_url(f["object_name"])
            return {
                "playlist_name": "全部音乐",
                "description": "AI 歌单生成失败，已列出所有音频文件",
                "songs": audio_files,
            }

