"""
downloader.py — 媒体下载处理

从工具返回的 URL 中下载图片/视频到本地 session assets 目录，
生成缩略图，并返回内联 HTML 供前端渲染。
"""
import os
import re
import uuid as _uuid

import httpx  # type: ignore[import]

try:
    from PIL import Image  # type: ignore[import]
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False


async def process_media_downloads(text: str, session_id: str, session_dir: str) -> str:
    """统一处理工具返回中的媒体 URL：下载图片/视频到本地 assets 并生成内联 HTML。"""
    urls = re.findall(r'https?://[^\]\s"\'\\]+', text)
    if not urls:
        return text

    assets_dir = os.path.join(session_dir, "assets")
    os.makedirs(assets_dir, exist_ok=True)
    replaced_text = text
    has_image = False
    has_video = False

    async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
        for url in urls:
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    continue

                content_type = resp.headers.get("Content-Type", "")
                media_id = str(_uuid.uuid4())[:8]

                # ---- 图片处理 ----
                if content_type.startswith("image/"):
                    ext = content_type.split("/")[-1].split(";")[0]
                    if ext not in ["jpeg", "png", "gif", "webp"]:
                        ext = "png"

                    filename = f"img_{media_id}.{ext}"
                    thumb_name = f"img_{media_id}_thumb.{ext}"
                    full_path = os.path.join(assets_dir, filename)
                    thumb_path = os.path.join(assets_dir, thumb_name)

                    with open(full_path, "wb") as f:
                        f.write(resp.content)

                    if _HAS_PIL:
                        try:
                            img = Image.open(full_path)
                            img.thumbnail((300, 300))
                            if img.mode != "RGB" and ext == "jpeg":
                                img = img.convert("RGB")
                            img.save(thumb_path)
                        except Exception as e:
                            print(f"[MediaDL] 缩略图生成失败: {e}")
                            thumb_name = filename
                    else:
                        thumb_name = filename

                    render_html = (
                        f'<a href="/assets/{session_id}/assets/{filename}" target="_blank">'
                        f'<img src="/assets/{session_id}/assets/{thumb_name}" alt="AI 生成图片" '
                        f"onerror=\"if(this.src!='/assets/{session_id}/assets/{filename}')this.src='/assets/{session_id}/assets/{filename}';\" />"
                        f"</a>"
                    )
                    replaced_text = replaced_text.replace(url, render_html)
                    has_image = True
                    print(f"[MediaDL] 图片已下载: {filename}")

                # ---- 视频处理 ----
                elif content_type.startswith("video/") or any(
                    ext in url.lower() for ext in [".mp4", ".webm", ".mov"]
                ):
                    ext = "mp4"
                    if content_type.startswith("video/"):
                        ext = content_type.split("/")[-1].split(";")[0]
                        if ext not in ["mp4", "webm", "mov"]:
                            ext = "mp4"

                    filename = f"vid_{media_id}.{ext}"
                    full_path = os.path.join(assets_dir, filename)

                    with open(full_path, "wb") as f:
                        f.write(resp.content)

                    render_html = (
                        f'<video controls src="/assets/{session_id}/assets/{filename}" '
                        f'style="max-width:100%;border-radius:12px;"></video>'
                    )
                    replaced_text = replaced_text.replace(url, render_html)
                    has_video = True
                    print(f"[MediaDL] 视频已下载: {filename}")

            except Exception as e:
                print(f"[MediaDL] 媒体下载失败: {url}, Error: {e}")

    # 追加指令让 LLM 正确输出 HTML
    if has_image or has_video:
        media_type = "图片" if has_image else "视频"
        replaced_text += (
            f"\n【重要指令】拦截成功！{media_type}已下载到本地。"
            f"请简要描述生成内容即可，不要输出任何链接或HTML代码。"
        )

    return replaced_text
