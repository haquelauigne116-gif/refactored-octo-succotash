"""
manager.py — MinIOManager 门面层

组合 storage / index / metadata / tagger / search / music 模块，
对外暴露与旧 minio_manager.py 完全一致的公开方法。
"""
import hashlib
import io
import logging
import os
import threading
from datetime import datetime

from backend.config import (  # type: ignore[import]
    MINIO_INDEX_FILE, MINIO_INDEX_DB, load_minio_config,
)

from .index import FileIndex
from .storage import MinIOStorage
from .metadata import extract_file_metadata
from .tagger import generate_categorized_tags, flatten_categorized_tags
from . import search as _search
from . import music as _music

logger = logging.getLogger(__name__)


class MinIOManager:
    """MinIO 文件管理器 — 对外 API 与旧版完全兼容"""

    def __init__(self):
        cfg = load_minio_config()

        # 存储层
        self._storage = MinIOStorage(cfg)
        self.enabled = self._storage.enabled
        self.client = self._storage.client  # 兼容 server.py 中部分直接访问

        # 索引层（自动迁移旧 JSON）
        self._index = FileIndex(
            db_path=MINIO_INDEX_DB,
            json_path=MINIO_INDEX_FILE if os.path.exists(MINIO_INDEX_FILE) else "",
        )

        # 后台同步索引
        if self.enabled:
            threading.Thread(target=self._sync_index, daemon=True).start()

    @property
    def bucket(self) -> str:
        return self._storage.bucket

    # ========== 索引同步 ==========

    def _sync_index(self) -> None:
        """后台与 MinIO 做同步校验（启动时执行一次）"""
        try:
            remote_names: set[str] = set()
            for obj in self._storage.list_objects():
                remote_names.add(obj.object_name)
                if obj.object_name not in self._index.list_names():
                    stat = self._storage.stat_object(obj.object_name)
                    self._index.put({
                        "object_name": obj.object_name,
                        "original_name": obj.object_name,
                        "description": "",
                        "tags": [],
                        "content_type": stat.content_type or "",
                        "size": stat.size or 0,
                        "uploaded_at": "",
                        "tagging_status": "unknown",
                    })

            stale = self._index.sync_remove_stale(remote_names)
            if stale:
                logger.info(f"[MinIO] 索引清理了 {len(stale)} 个过期条目")

            count = len(self._index.list_names())
            logger.info(f"[MinIO] 索引同步完成: {count} 个文件")

            # 同步完成后，检查是否需要迁移旧标签
            try:
                migrated = self.migrate_tags_to_categorized()
                if migrated > 0:
                    logger.info(f"[MinIO] 自动迁移了 {migrated} 个文件的标签分类")
            except Exception as me:
                logger.warning(f"[MinIO] 自动标签迁移失败: {me}")

        except Exception as e:
            logger.error(f"[MinIO] 索引同步失败: {e}")

    # ========== 核心操作 ==========

    def upload_fast(
        self, filename: str, file_data: bytes, content_type: str,
        description: str = "",
    ) -> dict:
        """快速上传：写入 MinIO（编号命名），索引记录信息，立即返回"""
        if not self.enabled:
            raise RuntimeError("MinIO 未配置")

        ext = os.path.splitext(filename)[1]
        file_id = self._index.next_id()
        object_name = f"{file_id:05d}{ext}"
        uploaded_at = datetime.now().isoformat()

        self._storage.put_object(object_name, file_data, content_type)
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
        self._index.put(entry)
        return entry

    def process_tags(
        self, object_name: str, filename: str,
        content_type: str, description: str = "",
    ) -> dict:
        """后台处理：提取元信息 → AI 打分类标签 → 更新索引"""
        temp_dir = os.path.join("data", "temp")
        os.makedirs(temp_dir, exist_ok=True)
        safe_name = (
            hashlib.md5(object_name.encode("utf-8")).hexdigest()
            + os.path.splitext(filename)[1]
        )
        temp_path = os.path.join(temp_dir, "tmp_meta_" + safe_name)

        try:
            self._storage.fget_object(object_name, temp_path)

            # 1. 提取文件自带元信息
            file_meta = extract_file_metadata(temp_path, filename, content_type)
            logger.info(f"[MinIO] 元信息提取完成: {filename} → {file_meta}")

            # 2. 联网搜索补充音乐元信息 + 歌词
            if file_meta.get("file_type") == "audio":
                try:
                    from backend.music_metadata_search import search_music_metadata  # type: ignore[import]
                    online_meta = search_music_metadata(
                        title=file_meta.get("title", ""),
                        artists=file_meta.get("artists"),
                        album=file_meta.get("album", ""),
                        filename=filename,
                        file_path=temp_path,
                    )
                    if online_meta:
                        for key in ("title", "album", "genre", "year"):
                            if not file_meta.get(key) and online_meta.get(key):
                                file_meta[key] = online_meta[key]
                        if not file_meta.get("artists") and online_meta.get("artists"):
                            file_meta["artists"] = online_meta["artists"]
                        for key in ("lyrics", "lyricist", "composer",
                                    "release_date", "source"):
                            if online_meta.get(key):
                                file_meta[key] = online_meta[key]
                        # 如果歌词被写入了临时文件，重新上传到 MinIO
                        if online_meta.get("lyrics") and os.path.isfile(temp_path):
                            try:
                                with open(temp_path, "rb") as f:
                                    data = f.read()
                                self._storage.put_object(
                                    object_name, data, content_type
                                )
                                logger.info(
                                    f"[MinIO] 含歌词的音频已重新上传: {object_name}"
                                )
                            except Exception as ue:
                                logger.warning(f"[MinIO] 重新上传失败: {ue}")
                        logger.info(f"[MinIO] 联网元信息补充完成: {filename}")
                except Exception as e:
                    logger.warning(
                        f"[MinIO] 联网元信息搜索失败 (不影响上传): {e}"
                    )

            # 3. AI 打分类标签（含 Last.fm 社区标签）
            lastfm_tags = file_meta.pop("lastfm_tags", None) if file_meta else None
            ai_result = generate_categorized_tags(
                filename, description, file_meta,
                lastfm_tags=lastfm_tags,
            )
            file_date = str(ai_result.pop("file_date", "") or "")
            categorized_tags = ai_result
            tags = flatten_categorized_tags(categorized_tags)

            # ★ A3: 歌词分离 — 写入索引前移除 lyrics
            file_meta.pop("lyrics", None)

            # 4. 更新索引
            self._index.update_tags(
                object_name, tags, file_meta,
                categorized_tags=categorized_tags,
                file_date=file_date,
            )

            logger.info(
                f"[MinIO] 标签处理完成: {object_name} "
                f"分类标签: {categorized_tags} 日期: {file_date}"
            )
            return {
                "object_name": object_name,
                "original_name": filename,
                "tags": tags,
                "categorized_tags": categorized_tags,
                "file_date": file_date,
                "file_meta": file_meta,
            }
        except Exception as e:
            logger.error(f"[MinIO] 标签处理失败: {object_name} ({e})")
            return {
                "object_name": object_name,
                "original_name": filename,
                "tags": [],
                "categorized_tags": {},
                "file_meta": {},
            }
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    def upload(
        self, filename: str, file_data: bytes, content_type: str,
        description: str = "",
    ) -> dict:
        """同步上传（兼容旧调用）：快速上传 + 同步处理标签"""
        entry = self.upload_fast(filename, file_data, content_type, description)
        result = self.process_tags(
            entry["object_name"], filename, content_type, description
        )
        entry["tags"] = result["tags"]
        entry["categorized_tags"] = result.get("categorized_tags", {})
        entry["file_meta"] = result["file_meta"]
        entry["tagging_status"] = "done"
        return entry

    def upload_batch(
        self, files: list[tuple[str, bytes, str, str]],
    ) -> list[dict]:
        """批量快速上传多个文件，一次性保存索引"""
        if not self.enabled:
            raise RuntimeError("MinIO 未配置")

        entries: list[dict] = []
        start_id = self._index.next_id()
        
        for i, (filename, file_data, content_type, description) in enumerate(files):
            ext = os.path.splitext(filename)[1]
            file_id = start_id + i
            object_name = f"{file_id:05d}{ext}"
            uploaded_at = datetime.now().isoformat()

            try:
                self._storage.put_object(object_name, file_data, content_type)
            except Exception as e:
                logger.error(f"[MinIO] 批量上传失败 ({filename}): {e}")
                continue

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
            entries.append(entry)
            logger.info(
                f"[MinIO] 批量上传 {len(entries)}: {object_name} ← {filename}"
            )

        if entries:
            self._index.put_batch(entries)

        return entries

    # ========== 查询操作 ==========

    def list_files(self) -> list[dict]:
        """列出所有文件"""
        if not self.enabled:
            return []
        return self._index.list_all()

    def search(self, query: str) -> list[dict]:
        """关键词搜索"""
        return _search.keyword_search(query, self.list_files())

    def ai_search(self, prompt: str) -> dict:
        """AI 智能检索文件"""
        if not self.enabled:
            raise RuntimeError("MinIO 未配置")
        return _search.ai_search(
            prompt,
            self.list_files(),
            self._index.build_tag_pool(),
            lambda obj_name: self._storage.get_download_url(obj_name, force_download=True),
        )

    def get_tag_pool(self) -> dict[str, list[str]]:
        """获取全局标签池"""
        return self._index.build_tag_pool()

    def get_music_tag_pool(self) -> dict[str, list[str]]:
        """获取音乐专用标签池（仅从音频文件汇总）"""
        return self._index.build_music_tag_pool()

    def delete(self, object_name: str) -> bool:
        """删除文件"""
        if not self.enabled:
            return False
        try:
            self._storage.remove_object(object_name)
            self._index.remove(object_name)
            logger.info(f"[MinIO] 已删除: {object_name}")
            return True
        except Exception as e:
            logger.error(f"[MinIO] 删除失败: {e}")
            return False

    def get_download_url(
        self, object_name: str,
        force_download: bool = False,
        filename: str | None = None,
    ) -> str:
        """生成预签名下载 URL"""
        return self._storage.get_download_url(
            object_name, force_download, filename
        )

    # ========== 标签迁移 ==========

    def migrate_tags_to_categorized(self) -> int:
        """将旧的扁平 tags 迁移为 categorized_tags"""
        to_migrate = self._index.get_unmigrated()
        if not to_migrate:
            return 0

        logger.info(f"[MinIO] 开始迁移 {len(to_migrate)} 个文件的标签分类")
        updates: list[tuple[str, dict, list[str]]] = []

        for object_name, entry in to_migrate:
            try:
                categorized_tags = generate_categorized_tags(
                    filename=entry.get("original_name", object_name),
                    description=entry.get("description", ""),
                    file_meta=entry.get("file_meta"),
                )
                flat_tags = flatten_categorized_tags(categorized_tags)
                updates.append((object_name, categorized_tags, flat_tags))
                logger.info(
                    f"[MinIO] 迁移完成 ({len(updates)}/{len(to_migrate)}): "
                    f"{object_name}"
                )
            except Exception as e:
                logger.warning(f"[MinIO] 迁移失败 {object_name}: {e}")

        if updates:
            self._index.bulk_update_categorized(updates)
            logger.info(
                f"[MinIO] 标签迁移完成: {len(updates)}/{len(to_migrate)} 个文件"
            )

        return len(updates)

    # ========== 音乐功能 ==========

    def _is_audio(self, content_type: str, filename: str) -> bool:
        return _music.is_audio(content_type, filename)

    def search_audio(self) -> list[dict]:
        return _music.filter_audio(self.list_files())

    def get_cover_art_file(self, object_name: str) -> str | None:
        return _music.get_cover_art_file(object_name, self._storage)

    def get_lyrics(self, object_name: str) -> str:
        return _music.get_lyrics(object_name, self._storage)

    def reindex_cover_art(self) -> int:
        return _music.reindex_cover_art(
            self.search_audio(), self._index, self._storage
        )

    def generate_playlist(self, prompt: str) -> dict:
        if not self.enabled:
            raise RuntimeError("MinIO 未配置")
        return _music.generate_playlist(
            prompt, self.search_audio(), self._storage.get_download_url,
            music_tag_pool=self.get_music_tag_pool(),
        )
