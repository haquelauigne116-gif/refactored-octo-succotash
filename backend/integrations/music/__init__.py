"""
music — 音乐元信息

- lastfm_client: Last.fm 标签获取
- metadata_search: 音乐元信息搜索
"""
from .lastfm_client import get_music_tags  # noqa: F401
from .metadata_search import search_music_metadata  # noqa: F401
