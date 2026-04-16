"""
bing_search.py — Bing 搜索引擎（免费，无需 API Key，国内可用）

通过抓取 Bing 搜索页面提取搜索结果。
提供 OpenAI function 工具定义 + 异步搜索执行。
"""
import re
from html import unescape

import httpx  # type: ignore[import]


# ====== OpenAI function 工具定义 ======

BING_SEARCH_TOOL_DEF: dict = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "搜索互联网获取最新信息、新闻、实时数据等。使用 Bing 搜索引擎。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词"
                },
                "max_results": {
                    "type": "integer",
                    "description": "返回的最大结果数量，默认 8",
                }
            },
            "required": ["query"]
        }
    }
}


# ====== 搜索实现 ======

async def bing_search(query: str, max_results: int = 8) -> str:
    """通过抓取 Bing 搜索页面获取搜索结果（免费、无需 API Key、国内可用）。"""
    print(f"[BingSearch] 执行搜索: '{query}' (max_results={max_results})")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(
                "https://www.bing.com/search",
                params={"q": query, "count": str(max_results)},
                headers=headers,
            )
            if resp.status_code != 200:
                return f"搜索请求失败 (HTTP {resp.status_code})"

            html = resp.text
            results = []

            # 主解析：b_algo 块
            algo_blocks = re.findall(r'<li class="b_algo"[^>]*>(.*?)</li>', html, re.DOTALL)
            for block in algo_blocks[:max_results]:
                title_match = re.search(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
                if not title_match:
                    continue

                url = title_match.group(1)
                title = re.sub(r'<[^>]+>', '', title_match.group(2)).strip()
                title = unescape(title)

                snippet = ""
                snippet_match = re.search(
                    r'<p[^>]*class="[^"]*b_lineclamp[^"]*"[^>]*>(.*?)</p>', block, re.DOTALL
                )
                if not snippet_match:
                    snippet_match = re.search(
                        r'<div class="b_caption"[^>]*>.*?<p[^>]*>(.*?)</p>', block, re.DOTALL
                    )
                if not snippet_match:
                    snippet_match = re.search(r'<p>(.*?)</p>', block, re.DOTALL)

                if snippet_match:
                    snippet = re.sub(r'<[^>]+>', '', snippet_match.group(1)).strip()
                    snippet = unescape(snippet)

                if title and url and not url.startswith("javascript:"):
                    results.append({"title": title, "url": url, "snippet": snippet})

            # 备用解析：h2 + a 块
            if not results:
                link_blocks = re.findall(
                    r'<h2[^>]*>.*?<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>.*?</h2>',
                    html, re.DOTALL,
                )
                for url, title_html in link_blocks[:max_results]:
                    title = re.sub(r'<[^>]+>', '', title_html).strip()
                    title = unescape(title)
                    if title:
                        results.append({"title": title, "url": url, "snippet": ""})

            if not results:
                return "未找到相关搜索结果。请尝试修改关键词后重试。"

            output_parts = [f"Bing 搜索结果 ({len(results)} 条):\n"]
            for i, r in enumerate(results, 1):
                output_parts.append(f"{i}. {r['title']}")
                output_parts.append(f"   链接: {r['url']}")
                if r["snippet"]:
                    output_parts.append(f"   摘要: {r['snippet']}")
                output_parts.append("")

            result_text = "\n".join(output_parts)
            print(f"[BingSearch] 搜索成功: 找到 {len(results)} 条结果")
            return result_text

    except httpx.TimeoutException:
        return "搜索超时，请稍后重试。"
    except Exception as e:
        print(f"[BingSearch] 搜索异常: {e}")
        return f"搜索失败: {str(e)}"
