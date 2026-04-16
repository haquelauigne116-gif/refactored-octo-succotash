"""
storage.py — MinIO 存储操作封装

只负责与 MinIO 的交互：连接、上传、下载、删除、预签名 URL。
"""
import io
import logging
import urllib.parse
from datetime import timedelta

from minio import Minio  # type: ignore[import]
from minio.error import S3Error  # type: ignore[import]

logger = logging.getLogger(__name__)


class MinIOStorage:
    """MinIO 对象存储操作封装"""

    def __init__(self, config: dict):
        self.bucket = config.get("bucket", "ai-assistant")
        self.enabled = bool(config.get("endpoint"))
        self.client: Minio | None = None

        if not self.enabled:
            logger.warning("[MinIO] 未配置，文件管理功能不可用")
            return

        try:
            self.client = Minio(
                endpoint=config["endpoint"],
                access_key=config.get("access_key", ""),
                secret_key=config.get("secret_key", ""),
                secure=config.get("secure", False),
            )
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
                logger.info(f"[MinIO] 创建 bucket: {self.bucket}")
            logger.info(f"[MinIO] 连接成功: {config['endpoint']}/{self.bucket}")
        except Exception as e:
            logger.error(f"[MinIO] 连接失败: {e}")
            self.enabled = False
            self.client = None

    def put_object(
        self, object_name: str, data: bytes, content_type: str
    ) -> None:
        """上传文件到 MinIO"""
        assert self.client is not None
        self.client.put_object(
            self.bucket,
            object_name,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )

    def put_object_from_file(
        self, object_name: str, file_path: str, content_type: str
    ) -> None:
        """从本地文件直接上传到 MinIO（零内存拷贝）"""
        assert self.client is not None
        self.client.fput_object(
            self.bucket,
            object_name,
            file_path,
            content_type=content_type,
        )

    def fget_object(self, object_name: str, file_path: str) -> None:
        """将 MinIO 对象下载到本地文件"""
        assert self.client is not None
        self.client.fget_object(self.bucket, object_name, file_path)

    def remove_object(self, object_name: str) -> None:
        """从 MinIO 删除对象"""
        assert self.client is not None
        self.client.remove_object(self.bucket, object_name)

    def stat_object(self, object_name: str):
        """获取对象元信息"""
        assert self.client is not None
        return self.client.stat_object(self.bucket, object_name)

    def list_objects(self) -> list:
        """列出 bucket 中所有对象"""
        assert self.client is not None
        return list(self.client.list_objects(self.bucket))

    def get_download_url(
        self,
        object_name: str,
        force_download: bool = False,
        filename: str | None = None,
    ) -> str:
        """生成预签名下载 URL（有效期 1 小时）"""
        if not self.enabled or self.client is None:
            return ""
        try:
            from typing import Any

            kwargs: dict[str, Any] = {"expires": timedelta(hours=1)}
            if force_download:
                fname = filename if filename else object_name.split("/")[-1]
                encoded_name = urllib.parse.quote(fname)
                kwargs["response_headers"] = {
                    "response-content-disposition": (
                        f"attachment; filename*=UTF-8''{encoded_name}"
                    )
                }
            return self.client.presigned_get_object(
                self.bucket, object_name, **kwargs
            )
        except Exception as e:
            logger.error(f"[MinIO] 生成下载链接失败: {e}")
            return ""
