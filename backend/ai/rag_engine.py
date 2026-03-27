"""
rag_engine.py — 双层 RAG 检索引擎 (关键词图 + BM25 模糊搜索)
             + 统一意图分析（RAG 检索 + 定时任务意图，合并为一次 API 调用）
"""
import os
import re
import math
import json
from datetime import datetime
from collections import Counter, defaultdict

from backend.config import KNOWLEDGE_DIR, get_client, APP_SETTINGS  # type: ignore[import]


class RAGEngine:
    """双层检索引擎：关键词倒排索引 + BM25 模糊评分"""

    CHUNK_SIZE = 500
    CHUNK_OVERLAP = 50
    BM25_K1 = 1.5
    BM25_B = 0.75
    KEYWORD_WEIGHT = 0.4
    BM25_WEIGHT = 0.6

    def __init__(self):
        self.chunks: list[dict] = []          # [{"id": int, "text": str, "source": str}]
        self.inverted_index: dict[str, set] = defaultdict(set)  # token → {chunk_ids}
        self.doc_tokens: list[list[str]] = []
        self.avg_doc_len: float = 0.0
        self.df: Counter = Counter()
        self.reload()

    # ====== 知识库加载 ======

    def reload(self):
        """重新扫描知识库目录，重建索引"""
        self.chunks = []
        self.inverted_index = defaultdict(set)
        self.doc_tokens = []
        chunk_id: int = 0

        if not os.path.exists(KNOWLEDGE_DIR):
            return

        for filename in os.listdir(KNOWLEDGE_DIR):
            if not (filename.endswith(".txt") or filename.endswith(".md")):
                continue
            filepath = os.path.join(KNOWLEDGE_DIR, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content: str = f.read()
            except Exception as e:
                print(f"[RAG] 读取 {filename} 失败: {e}")
                continue

            # 分块（滑动窗口）
            start = 0
            while start < len(content):
                end = min(start + self.CHUNK_SIZE, len(content))
                text: str = content[start:end]  # type: ignore[index]
                if len(text.strip()) > 20:
                    self.chunks.append({"id": chunk_id, "text": text, "source": filename})
                    chunk_id += 1  # type: ignore[operator]
                start += (self.CHUNK_SIZE - self.CHUNK_OVERLAP)

        # 预处理：分词 + 构建倒排索引 + DF
        self.doc_tokens = [self._tokenize(c["text"]) for c in self.chunks]
        self.df = Counter()

        for cid, tokens in enumerate(self.doc_tokens):
            unique = set(tokens)
            self.df.update(unique)
            for token in unique:
                self.inverted_index[token].add(cid)

        total_len = sum(len(t) for t in self.doc_tokens)
        self.avg_doc_len = total_len / len(self.doc_tokens) if self.doc_tokens else 1.0

        print(f"[RAG] 知识库加载完毕: {len(self.chunks)} 个文本块, {len(self.inverted_index)} 个索引词")

    # ====== 双层检索 ======

    def search(self, query: str, top_k: int = 3) -> list[str]:
        """双层搜索：关键词图 + BM25 模糊，返回合并排名的 top_k 文本"""
        self.reload()  # 每次搜索前重新加载，感知新文件
        if not self.chunks:
            return []

        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        N = len(self.chunks)

        # --- Layer 1: 关键词倒排索引精确命中 ---
        keyword_scores = [0.0] * N
        for token in query_tokens:
            if token in self.inverted_index:
                hit_ids = self.inverted_index[token]
                # 命中越少的词越有区分度，加权 = 1/hit_count
                boost = 1.0 / len(hit_ids) if hit_ids else 0
                for cid in hit_ids:
                    keyword_scores[cid] += boost

        # --- Layer 2: BM25 模糊评分 ---
        bm25_scores = [0.0] * N
        query_counts = Counter(query_tokens)

        for cid, doc_toks in enumerate(self.doc_tokens):
            if not doc_toks:
                continue
            doc_len = len(doc_toks)
            doc_counts = Counter(doc_toks)
            score = 0.0
            for q_tok, q_freq in query_counts.items():
                if q_tok not in doc_counts:
                    continue
                tf = doc_counts[q_tok]
                df_val = self.df.get(q_tok, 0)
                # IDF: log((N - df + 0.5) / (df + 0.5) + 1)
                idf = math.log((N - df_val + 0.5) / (df_val + 0.5) + 1)
                # BM25 TF 归一化
                tf_norm = (tf * (self.BM25_K1 + 1)) / (
                    tf + self.BM25_K1 * (1 - self.BM25_B + self.BM25_B * doc_len / self.avg_doc_len)
                )
                score += idf * tf_norm
            bm25_scores[cid] = score

        # --- 归一化 & 合并 ---
        kw_max = max(keyword_scores) if max(keyword_scores) > 0 else 1.0
        bm_max = max(bm25_scores) if max(bm25_scores) > 0 else 1.0

        final_scores = []
        for cid in range(N):
            kw_norm = keyword_scores[cid] / kw_max
            bm_norm = bm25_scores[cid] / bm_max
            combined = self.KEYWORD_WEIGHT * kw_norm + self.BM25_WEIGHT * bm_norm
            if combined > 0:
                final_scores.append((cid, combined))

        final_scores.sort(key=lambda x: x[1], reverse=True)
        top_results = final_scores[:top_k]  # type: ignore[index]
        return [self.chunks[cid]["text"] for cid, _ in top_results]

    # ====== 统一意图分析（RAG + 定时任务，一次 API 调用） ======

    def analyze_intent(self, messages: list[dict], current_query: str) -> dict:
        """
        统一门卫：一次 API 调用同时完成多个判断：
        1. 是否需要检索知识库？
        2. 用户是否在请求创建定时任务？
        3. 用户是否想从MinIO中查找文件？
        4. 用户是否需要调用阿里云MCP服务？
        5. 用户是否在创建/查询日程？

        返回: {"rag_query": str, "task_intent": dict | None, "file_search_query": "", "mcp_intent": "", "schedule_intent": None}
        """
        result: dict = {"rag_query": "", "task_intent": None, "file_search_query": "", "mcp_intent": "", "schedule_intent": None}

        recent: list[dict] = messages[-6:]  # type: ignore[index]
        context_lines = []
        for m in recent:
            role_label = "用户" if m["role"] == "user" else "AI"
            context_lines.append(f"{role_label}: {m['content'][:200]}")
        context_lines.append(f"用户（最新）: {current_query}")
        context_text = "\n".join(context_lines)

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        weekday = weekday_names[datetime.now().weekday()]

        system_prompt = (
            "你是一个多功能意图分析专家。请阅读对话上下文，同时完成以下三个判断：\n\n"
            "【判断1 - 知识库检索】\n"
            "用户最新的话是否需要查阅资料？\n"
            "- 如果只是闲聊（打招呼、道谢等），输出：RAG: NONE\n"
            "- 如果需要查资料，结合上下文还原代词，提取 3-5 个核心搜索关键词，输出：RAG: 关键词1 关键词2 关键词3\n\n"
            "【判断2 - 定时任务】\n"
            "用户是否在请求设置定时提醒、闹钟或定时任务？\n"
            "- 如果没有涉及，输出：TASK: NONE\n"
            "- 如果有，输出 TASK: 后跟 JSON，格式如下：\n"
            'TASK: {"task_name": "简短名≤15字", "trigger_type": "date|interval|cron", '
            '"trigger_args": {...}, "action_prompt": "到时间后要做什么"}\n\n'
            "【判断3 - 查找文件】\n"
            "用户是否希望从云端网盘(MinIO)中查找或是过滤特定的文件、照片、文档、音乐等？\n"
            "- 如果没有涉及查找文件，输出：FILE_SEARCH: NONE\n"
            "- 如果用户想找文件，提取用户的查找描述（例如“找一张风景图” → “风景图”），输出：FILE_SEARCH: 查询描述\n\n"
            "【判断4 - 外部工具/MCP服务】\n"
            "分析用户是否明显需要以下一种外部服务（单选）：\n"
            "- 需要绘制、生成一幅图片（如“帮我画个猫”）：MCP: Z_IMAGE\n"
            "- 明确提到使用即梦、Jimeng、火山引擎来生成图片或视频（如“用即梦画图”、“即梦生成视频”）：MCP: JIMENG\n"
            "- 查询地理路线、地点周边、地图导航或当前天气（如“查下北京天气”、“怎么去天安门”）：MCP: AMAP\n"
            "- 需要联网查询最新新闻资讯或实时信息（如“今天有什么新闻”、“搜索一下XXX”）：MCP: WEB_SEARCH\n"
            "- 明确要求把文字合成语音发出来或朗读文字（如“朗读这句话”、“转成语音”）：MCP: TTS\n"
            "- 其他所有情况（不需要以上五种服务，或仅是正常文本聊天、知识库聊天）：MCP: NONE\n\n"
            "【判断5 - 日程管理】\n"
            "用户是否在创建、修改或查询日程、会议、活动、安排？（注意：定时提醒属于判断2，日程是有起止时间的事件）\n"
            "- 如果没有涉及日程，输出：SCHEDULE: NONE\n"
            "- 如果用户要创建日程，输出 SCHEDULE: 后跟 JSON，格式：\n"
            'SCHEDULE: {"action": "create", "title": "简短标题", "start_time": "YYYY-MM-DDTHH:MM:SS", '
            '"end_time": "YYYY-MM-DDTHH:MM:SS", "category": "工作|个人|学习|会议|健康|其他", '
            '"location": "地点或空", "all_day": false}\n'
            "- 如果用户没说结束时间，默认持续1小时\n"
            "- 如果用户说'全天'或没有具体时间，设 all_day 为 true\n\n"
            "⚠️ trigger_type 选择规则（非常重要，必须严格遵守）：\n"
            "- date（一次性）：「X分钟后提醒我」「半小时后叫我」「明天下午3点提醒」→ 计算出具体时间点\n"
            "- interval（循环间隔）：「每隔30分钟提醒一次」「每小时提醒喝水」→ 定期重复\n"
            "- cron（每天/每周固定时间）：「每天早上9点提醒」「工作日下午6点提醒下班」→ 日历规律\n\n"
            "trigger_args 规则：\n"
            '- date → {"run_date": "YYYY-MM-DD HH:MM:SS"}（根据当前时间计算）\n'
            '- interval → {"seconds": N} 或 {"minutes": N} 或 {"hours": N}\n'
            '- cron → {"hour": "H", "minute": "M"} 可选 "day_of_week": "mon-fri"\n\n'
            f"当前时间: {now_str}（{weekday}）\n\n"
            "【输出格式】严格输出五行，不要有任何其他内容：\n"
            "RAG: 关键词或NONE\n"
            "TASK: JSON或NONE\n"
            "FILE_SEARCH: 查找描述或NONE\n"
            "MCP: Z_IMAGE/JIMENG/AMAP/WEB_SEARCH/TTS/NONE\n"
            "SCHEDULE: JSON或NONE"
        )

        try:
            client = get_client(APP_SETTINGS["judge_provider"])
            resp = client.chat.completions.create(
                model=APP_SETTINGS["judge_model"],
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": context_text},
                ],
                temperature=0.1,
                stream=False,
            )
            answer = resp.choices[0].message.content.strip()
            print(f"[IntentAnalysis] 原始输出: {answer[:300]}")

            # 解析 RAG 行
            for line in answer.split("\n"):
                line = line.strip()
                if line.upper().startswith("RAG:"):
                    rag_val = line[4:].strip()
                    if rag_val.upper() != "NONE":
                        result["rag_query"] = rag_val
                        print(f"[IntentAnalysis] RAG 关键词: {rag_val}")
                    else:
                        print("[IntentAnalysis] RAG: 不需要检索")

                elif line.upper().startswith("TASK:"):
                    task_val = line[5:].strip()
                    if task_val.upper() == "NONE":
                        print("[IntentAnalysis] TASK: 无任务意图")
                    else:
                        try:
                            # 兼容 markdown 代码块
                            json_text = task_val
                            if "```" in json_text:
                                parts = json_text.split("```")
                                for part in parts:
                                    s = part.strip()
                                    if s.startswith("json"):
                                        s = s[4:].strip()
                                    if s.startswith("{"):
                                        json_text = s
                                        break
                            task_data = json.loads(json_text)
                            required = ["task_name", "trigger_type", "trigger_args", "action_prompt"]
                            if all(k in task_data for k in required) and task_data["trigger_type"] in ("date", "interval", "cron"):
                                result["task_intent"] = task_data
                                print(f"[IntentAnalysis] ✅ 任务意图: {task_data['task_name']}")
                            else:
                                print(f"[IntentAnalysis] TASK JSON 字段不完整: {task_data}")
                        except json.JSONDecodeError as e:
                            print(f"[IntentAnalysis] TASK JSON 解析失败: {e}")

                elif line.upper().startswith("FILE_SEARCH:"):
                    search_str = line[12:].strip()
                    if search_str.upper() != "NONE":
                        result["file_search_query"] = search_str
                        print(f"[IntentAnalysis] ✅ 查找文件意图: {search_str}")
                    else:
                        print("[IntentAnalysis] FILE_SEARCH: 无查找文件请求")

                elif line.upper().startswith("MCP:"):
                    mcp_val = line[4:].strip().upper()
                    if mcp_val != "NONE":
                        result["mcp_intent"] = mcp_val
                        print(f"[IntentAnalysis] ✅ 检测到 MCP 意图: {mcp_val}")
                    else:
                        print(f"[IntentAnalysis] MCP: 不需要外部服务")

                elif line.upper().startswith("SCHEDULE:"):
                    sch_val = line[9:].strip()
                    if sch_val.upper() == "NONE":
                        print("[IntentAnalysis] SCHEDULE: 无日程意图")
                    else:
                        try:
                            json_text = sch_val
                            if "```" in json_text:
                                parts = json_text.split("```")
                                for part in parts:
                                    s = part.strip()
                                    if s.startswith("json"):
                                        s = s[4:].strip()
                                    if s.startswith("{"):
                                        json_text = s
                                        break
                            sch_data = json.loads(json_text)
                            if sch_data.get("action") == "create" and "title" in sch_data and "start_time" in sch_data:
                                result["schedule_intent"] = sch_data
                                print(f"[IntentAnalysis] ✅ 日程意图: {sch_data['title']}")
                            else:
                                print(f"[IntentAnalysis] SCHEDULE JSON 字段不完整: {sch_data}")
                        except json.JSONDecodeError as e:
                            print(f"[IntentAnalysis] SCHEDULE JSON 解析失败: {e}")

        except Exception as e:
            print(f"[IntentAnalysis] 意图分析异常: {e}")
            # 降级：用用户原话作为 RAG 查询
            result["rag_query"] = current_query

        return result

    def retrieve_context(self, rag_query: str, top_k: int = 2) -> str:
        """
        根据已提取的搜索关键词执行检索，返回上下文字符串。
        传入空字符串则跳过检索。
        """
        if not rag_query:
            return ""

        docs = self.search(rag_query, top_k=top_k)

        if docs:
            print(f"[RAG] 检索到 {len(docs)} 个文本块")
            return "\n\n--- 相关知识库参考 ---\n" + "\n".join(docs)
        else:
            print("[RAG] 未找到相关文本")
            return ""

    # ====== 工具方法 ======

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """简单分词：中文按字、英文按词"""
        cleaned = re.sub(r'[^\w\u4e00-\u9fff]', ' ', text.lower())
        tokens = []
        for part in cleaned.split():
            if any('\u4e00' <= c <= '\u9fff' for c in part):
                tokens.extend(list(part))  # 中文逐字
            else:
                tokens.append(part)  # 英文整词
        return [t for t in tokens if t.strip()]
