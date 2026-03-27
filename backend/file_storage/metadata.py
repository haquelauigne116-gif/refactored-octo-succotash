"""
metadata.py — 文件元信息提取

从音频/图片/视频文件中提取元数据（EXIF、ID3 tags、GPS 等）。
"""
import json
import logging
import os

logger = logging.getLogger(__name__)


def _extract_audio_metadata(file_path: str, filename: str) -> dict:
    """从音频文件提取元信息 (title, artists, album, genre, duration)"""
    try:
        import mutagen  # type: ignore[import]
        from mutagen.id3 import ID3NoHeaderError  # type: ignore[import]  # noqa: F401
    except ImportError:
        logger.warning("[Meta] mutagen 未安装，跳过音频元信息提取")
        return {}

    result: dict = {}
    ext = os.path.splitext(filename)[1].lower()

    try:
        audio = mutagen.File(file_path)
        if audio is None:
            return {}

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
            split_artists: list[str] = []
            for a in artists:
                for sep in ["/", ";", "、", "&", ","]:
                    if sep in a:
                        split_artists.extend(
                            p.strip() for p in a.split(sep) if p.strip()
                        )
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

        if "Model" in exif:
            result["camera"] = str(exif["Model"]).strip()
        if "DateTimeOriginal" in exif:
            result["taken_at"] = str(exif["DateTimeOriginal"])
        elif "DateTime" in exif:
            result["taken_at"] = str(exif["DateTime"])

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
                lon = _dms_to_decimal(
                    gps["GPSLongitude"], gps["GPSLongitudeRef"]
                )
                result["longitude"] = lon

            if "latitude" in result and "longitude" in result:
                location = _reverse_geocode(
                    result["latitude"], result["longitude"]
                )
                if location:
                    result["location"] = location

    except Exception as e:
        logger.warning(f"[Meta] 图片 EXIF 提取失败 ({filename}): {e}")

    return result


def _extract_video_metadata(file_path: str, filename: str) -> dict:
    """从视频提取基本信息"""
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

        url = (
            f"https://nominatim.openstreetmap.org/reverse?"
            f"lat={lat}&lon={lon}&format=json&zoom=10&accept-language=zh"
        )
        req = urllib.request.Request(
            url, headers={"User-Agent": "XiaoYu-AI/1.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return data.get("display_name", "")[:50]
    except Exception as e:
        logger.warning(f"[Meta] 反向地理编码失败: {e}")
        return f"{lat:.4f},{lon:.4f}"


def extract_file_metadata(
    file_path: str, filename: str, content_type: str
) -> dict:
    """根据文件类型分发元信息提取"""
    ct = (content_type or "").lower()
    ext = os.path.splitext(filename)[1].lower()

    # 音频
    if ct.startswith("audio/") or ext in (
        ".mp3", ".wav", ".flac", ".ogg", ".aac", ".m4a", ".wma",
    ):
        return {"file_type": "audio", **_extract_audio_metadata(file_path, filename)}

    # 图片
    if ct.startswith("image/") or ext in (
        ".jpg", ".jpeg", ".png", ".tiff", ".heic", ".heif",
    ):
        return {"file_type": "image", **_extract_image_metadata(file_path, filename)}

    # 视频
    if ct.startswith("video/") or ext in (
        ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm",
    ):
        return {"file_type": "video", **_extract_video_metadata(file_path, filename)}

    return {"file_type": "other"}
