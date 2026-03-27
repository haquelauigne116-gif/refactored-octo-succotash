"""
file_storage — 模块化文件管理包

对外暴露 MinIOManager，保持与旧 minio_manager.py 完全兼容的 API。
"""
from .manager import MinIOManager  # noqa: F401

__all__ = ["MinIOManager"]
