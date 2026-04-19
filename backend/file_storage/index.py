"""
index.py — SQLite 索引层

替代原来的 minio_index.json 全量读写方案。
所有文件元信息以增量方式存入 SQLite，支持高效查询。
"""
import json
import logging
import os
import sqlite3
import threading

logger = logging.getLogger(__name__)


class FileIndex:
    """线程安全的 SQLite 文件索引"""

    def __init__(self, db_path: str, json_path: str = ""):
        self._db_path = db_path
        self._lock = threading.Lock()

        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._create_tables()

        # 自动迁移旧 JSON 索引
        if json_path and os.path.exists(json_path):
            self._migrate_from_json(json_path)

    def _create_tables(self) -> None:
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS files (
                    object_name     TEXT PRIMARY KEY,
                    original_name   TEXT NOT NULL DEFAULT '',
                    description     TEXT NOT NULL DEFAULT '',
                    content_type    TEXT NOT NULL DEFAULT '',
                    size            INTEGER NOT NULL DEFAULT 0,
                    uploaded_at     TEXT NOT NULL DEFAULT '',
                    file_date       TEXT NOT NULL DEFAULT '',
                    tagging_status  TEXT NOT NULL DEFAULT 'pending',
                    file_meta       TEXT NOT NULL DEFAULT '{}',
                    categorized_tags TEXT NOT NULL DEFAULT '{}',
                    tags            TEXT NOT NULL DEFAULT '[]'
                );
            """)

    # ========== CRUD ==========

    def put(self, entry: dict) -> None:
        """插入或更新一个文件条目"""
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO files
                   (object_name, original_name, description, content_type,
                    size, uploaded_at, file_date, tagging_status,
                    file_meta, categorized_tags, tags)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    entry["object_name"],
                    entry.get("original_name", ""),
                    entry.get("description", ""),
                    entry.get("content_type", ""),
                    entry.get("size", 0),
                    entry.get("uploaded_at", ""),
                    entry.get("file_date", ""),
                    entry.get("tagging_status", "pending"),
                    json.dumps(entry.get("file_meta", {}), ensure_ascii=False),
                    json.dumps(entry.get("categorized_tags", {}), ensure_ascii=False),
                    json.dumps(entry.get("tags", []), ensure_ascii=False),
                ),
            )
            self._conn.commit()

    def put_batch(self, entries: list[dict]) -> None:
        """批量插入文件条目（一次性提交）"""
        if not entries:
            return
        with self._lock:
            self._conn.executemany(
                """INSERT OR REPLACE INTO files
                   (object_name, original_name, description, content_type,
                    size, uploaded_at, file_date, tagging_status,
                    file_meta, categorized_tags, tags)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        e["object_name"],
                        e.get("original_name", ""),
                        e.get("description", ""),
                        e.get("content_type", ""),
                        e.get("size", 0),
                        e.get("uploaded_at", ""),
                        e.get("file_date", ""),
                        e.get("tagging_status", "pending"),
                        json.dumps(e.get("file_meta", {}), ensure_ascii=False),
                        json.dumps(e.get("categorized_tags", {}), ensure_ascii=False),
                        json.dumps(e.get("tags", []), ensure_ascii=False),
                    )
                    for e in entries
                ],
            )
            self._conn.commit()

    def remove(self, object_name: str) -> None:
        """删除一个文件条目"""
        with self._lock:
            self._conn.execute(
                "DELETE FROM files WHERE object_name = ?", (object_name,)
            )
            self._conn.commit()

    def get(self, object_name: str) -> dict | None:
        """查询单个文件条目"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM files WHERE object_name = ?", (object_name,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_all(self) -> list[dict]:
        """列出所有文件条目"""
        with self._lock:
            rows = self._conn.execute("SELECT * FROM files").fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_names(self) -> set[str]:
        """列出所有 object_name（轻量操作，用于同步）"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT object_name FROM files"
            ).fetchall()
        return {r["object_name"] for r in rows}

    def update_tags(
        self,
        object_name: str,
        tags: list[str],
        file_meta: dict | None = None,
        categorized_tags: dict | None = None,
        file_date: str = "",
    ) -> None:
        """更新文件的标签、元信息和日期"""
        sets: list[str] = [
            "tags = ?",
            "tagging_status = 'done'",
        ]
        params: list = [json.dumps(tags, ensure_ascii=False)]

        if categorized_tags is not None:
            sets.append("categorized_tags = ?")
            params.append(json.dumps(categorized_tags, ensure_ascii=False))
        if file_meta is not None:
            sets.append("file_meta = ?")
            params.append(json.dumps(file_meta, ensure_ascii=False))
        if file_date:
            sets.append("file_date = ?")
            params.append(file_date)

        params.append(object_name)

        with self._lock:
            self._conn.execute(
                f"UPDATE files SET {', '.join(sets)} WHERE object_name = ?",
                params,
            )
            self._conn.commit()

    def update_field(self, object_name: str, field: str, value) -> None:
        """更新单个字段（用于 cover_art 等场景）"""
        # 对于 JSON 字段内的子键，需要先读再写
        entry = self.get(object_name)
        if not entry:
            return
        if field.startswith("file_meta."):
            sub_key = field.split(".", 1)[1]
            meta = entry.get("file_meta", {})
            meta[sub_key] = value
            with self._lock:
                self._conn.execute(
                    "UPDATE files SET file_meta = ? WHERE object_name = ?",
                    (json.dumps(meta, ensure_ascii=False), object_name),
                )
                self._conn.commit()
        else:
            with self._lock:
                self._conn.execute(
                    f"UPDATE files SET {field} = ? WHERE object_name = ?",
                    (value, object_name),
                )
                self._conn.commit()

    # ========== 聚合查询 ==========

    def next_id(self) -> int:
        """生成下一个自增编号"""
        with self._lock:
            row = self._conn.execute("""
                SELECT MAX(CAST(
                    SUBSTR(object_name, 1, INSTR(object_name, '.') - 1)
                    AS INTEGER
                )) AS max_id FROM files
                WHERE object_name GLOB '[0-9]*.*'
            """).fetchone()
        max_id = row["max_id"] if row and row["max_id"] is not None else 0
        return int(max_id) + 1

    def build_tag_pool(self) -> dict[str, list[str]]:
        """汇总所有文件的 categorized_tags → 全局标签池 (限制数量，按频率排序)"""
        from collections import Counter
        pool: dict[str, Counter] = {
            "file_type": Counter(),
            "author": Counter(),
            "location": Counter(),
            "description": Counter(),
        }
        with self._lock:
            rows = self._conn.execute(
                "SELECT categorized_tags FROM files"
            ).fetchall()
        for row in rows:
            cats = json.loads(row["categorized_tags"] or "{}")
            for cat in pool:
                for tag in cats.get(cat, []):
                    if tag:
                        pool[cat][tag] += 1
                        
        # 限制各个类别的最大标签数量，避免超过 LLM 上下文
        limits = {
            "file_type": 50,
            "author": 200,
            "location": 100,
            "description": 200,
        }
        
        return {
            cat: [t for t, _ in counter.most_common(limits[cat])] 
            for cat, counter in pool.items()
        }

    def build_music_tag_pool(self) -> dict[str, list[str]]:
        """仅从音频文件汇总 categorized_tags → 音乐专用标签池 (限制数量，按频率排序)"""
        from collections import Counter
        pool: dict[str, Counter] = {
            "file_type": Counter(),
            "author": Counter(),
            "location": Counter(),
            "description": Counter(),
        }
        with self._lock:
            rows = self._conn.execute(
                """SELECT categorized_tags FROM files
                   WHERE content_type LIKE 'audio/%'
                   OR original_name LIKE '%.mp3'
                   OR original_name LIKE '%.flac'
                   OR original_name LIKE '%.wav'
                   OR original_name LIKE '%.ogg'
                   OR original_name LIKE '%.aac'
                   OR original_name LIKE '%.m4a'
                   OR original_name LIKE '%.wma'"""
            ).fetchall()
        for row in rows:
            cats = json.loads(row["categorized_tags"] or "{}")
            for cat in pool:
                for tag in cats.get(cat, []):
                    if tag:
                        pool[cat][tag] += 1
                        
        limits = {
            "file_type": 50,
            "author": 200,
            "location": 100,
            "description": 200,
        }
        
        return {
            cat: [t for t, _ in counter.most_common(limits[cat])] 
            for cat, counter in pool.items()
        }

    def get_unmigrated(self) -> list[tuple[str, dict]]:
        """获取需要迁移标签分类的条目（有 tags 但无 categorized_tags）"""
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM files
                   WHERE tagging_status = 'done'
                   AND (categorized_tags = '{}' OR categorized_tags = '')"""
            ).fetchall()
        return [(r["object_name"], self._row_to_dict(r)) for r in rows]

    def bulk_update_categorized(
        self, updates: list[tuple[str, dict, list[str]]]
    ) -> None:
        """批量更新 categorized_tags 和 tags"""
        with self._lock:
            self._conn.executemany(
                """UPDATE files
                   SET categorized_tags = ?, tags = ?
                   WHERE object_name = ?""",
                [
                    (
                        json.dumps(cats, ensure_ascii=False),
                        json.dumps(tags, ensure_ascii=False),
                        name,
                    )
                    for name, cats, tags in updates
                ],
            )
            self._conn.commit()

    # ========== 同步 ==========

    def sync_add(self, entries: list[dict]) -> None:
        """添加 MinIO 有但索引没有的文件"""
        self.put_batch(entries)

    def sync_remove_stale(self, remote_names: set[str]) -> list[str]:
        """删除索引中有但 MinIO 没有的条目，返回被清除的名称列表"""
        local_names = self.list_names()
        stale = local_names - remote_names
        if not stale:
            return []
        with self._lock:
            placeholders = ",".join("?" * len(stale))
            self._conn.execute(
                f"DELETE FROM files WHERE object_name IN ({placeholders})",
                list(stale),
            )
            self._conn.commit()
        return list(stale)

    # ========== 内部方法 ==========

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        """将 sqlite3.Row 转换为与旧 JSON 索引兼容的 dict"""
        d = dict(row)
        # 解析 JSON 字段
        for key in ("file_meta", "categorized_tags", "tags"):
            raw = d.get(key, "")
            if isinstance(raw, str):
                try:
                    d[key] = json.loads(raw) if raw else (
                        [] if key == "tags" else {}
                    )
                except json.JSONDecodeError:
                    d[key] = [] if key == "tags" else {}
        return d

    def _migrate_from_json(self, json_path: str) -> None:
        """从旧 minio_index.json 迁移数据到 SQLite"""
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or not data:
                return

            entries = []
            for obj_name, entry in data.items():
                entry["object_name"] = obj_name
                # 迁移时清理歌词（A3: 歌词分离）
                file_meta = entry.get("file_meta", {})
                if isinstance(file_meta, dict):
                    file_meta.pop("lyrics", None)
                    entry["file_meta"] = file_meta
                entries.append(entry)

            self.put_batch(entries)

            # 迁移完成后重命名旧文件为 .migrated
            migrated_path = json_path + ".migrated"
            os.rename(json_path, migrated_path)
            logger.info(
                f"[Index] 从 JSON 迁移 {len(entries)} 个文件到 SQLite，"
                f"旧文件已重命名为 {migrated_path}"
            )
        except Exception as e:
            logger.error(f"[Index] JSON → SQLite 迁移失败: {e}")

    def close(self) -> None:
        """关闭数据库连接"""
        with self._lock:
            self._conn.close()
