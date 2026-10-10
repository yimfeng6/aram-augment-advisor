"""用自然语言查符文 —— RAG 的最小实现，不用任何框架。

RAG = Retrieval-Augmented Generation（检索增强生成）

    检索  Retrieval    从一堆文档里，找出和问题最相关的几段
    增强  Augmented    把这几段塞进提示词
    生成  Generation   让模型**基于检索到的内容**回答

    为什么要「检索」？因为模型不知道你的私有数据。
    216 个符文的描述它没见过，直接问它只会编。
    把相关描述喂给它，它就能基于事实回答。

────────────────────────────────────────────────────────
两种检索方式的对比（这是本文件最值得看的部分）
────────────────────────────────────────────────────────

【TF-IDF】靠**字面重合**
    把文本拆成字符 n-gram 统计词频。
    所以「生命偷取」能搜到「生命偷取」，
    但「攻速」搜不到「攻击速度」—— 中间隔了个「击」字，
    没有 2~4 字的连续重合。

【语义向量】靠**意思相近**
    用预训练模型把文本编码成 512 维向量。
    在向量空间里，「攻速」和「攻击速度」距离很近，
    因为模型在海量语料上学过它们是同一个意思。

跑同一个问题对比一下：
    python rag.py "加攻速的符文"            # 语义（默认）
    python rag.py "加攻速的符文" --tfidf     # 字面

────────────────────────────────────────────────────────
用法
    python rag.py "哪些符文能回血"
    python rag.py "有什么加攻速的符文" --tfidf
"""

import os
import sqlite3
import sys
from pathlib import Path

import numpy as np
from openai import OpenAI
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

DB_PATH = Path("aramkit.db")
BASE_URL = os.environ.get("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1")
MODEL = os.environ.get("MIMO_MODEL", "mimo-v2.6-pro")

# 中文语义模型。第一次用会从 HuggingFace 下载，
# 国内要加镜像：HF_ENDPOINT=https://hf-mirror.com
EMBED_MODEL = "BAAI/bge-small-zh-v1.5"

# bge 系列模型建议给「查询」加一个指令前缀，检索效果更好。
# 注意：只加在查询上，文档不加 —— 这是模型训练时就定好的用法。
BGE_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："

TOP_K = 5


# ==================== 文档库 ====================
def load_docs() -> list[dict]:
    """从数据库读出全部符文，作为「文档库」。

    RAG 里的「文档」不一定是长文章 —— 一条数据库记录也算。
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT id, name, description FROM augments "
            "WHERE description != '' ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [{"id": r[0], "name": r[1], "text": r[2]} for r in rows]


# ==================== 索引：把文档变成向量 ====================
class Index:
    """
    符文索引。两种模式：
        mode="semantic"  语义向量（默认，懂同义词）
        mode="tfidf"     字面匹配（对比用）
    """

    def __init__(self, mode: str = "semantic"):
        self.mode = mode
        self.docs = load_docs()

        # ---- 文档怎么拼，实测有讲究 ----
        #
        # 试过三种拼法，用 5 个查询做了对比（命中"描述里真的有那个关键词"的比例）：
        #     名字：描述              16/25
        #     只用描述                17/25
        #     描述 + 符文名称：名字     20/25   ← 采用这个
        #
        # 名字放最后、并且加个"符文名称："的标签，效果最好。
        # （注意：这个标签每个文档都有，会让所有相似度分数整体抬高一点，
        #   但实测排序确实更好 —— 分数绝对值别太当真，看排序。）
        #
        # 教训：**别只拿一个问题调参**。第一版只试了"加攻速"，
        # C 看起来远好于 A；换 5 个查询跑完才发现差距没那么夸张。
        corpus = [f"{d['text']} 符文名称：{d['name']}" for d in self.docs]

        if mode == "semantic":
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(EMBED_MODEL)
            # normalize_embeddings=True 把向量长度归一化成 1。
            # 归一化之后，两个向量的**点积就等于余弦相似度** ——
            # 于是检索从「逐个算余弦」变成「一次矩阵乘法」。
            # 这就是向量数据库能毫秒级检索百万条的原因。
            self.matrix = self.model.encode(corpus, normalize_embeddings=True)
        else:
            self.vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 4))
            self.matrix = self.vectorizer.fit_transform(corpus)

    def search(self, query: str, top_k: int = TOP_K) -> list[dict]:
        """检索。整个流程本质就三步：向量化 → 算相似度 → 排序取前 K。"""
        if self.mode == "semantic":
            q = self.model.encode([BGE_QUERY_PREFIX + query],
                                  normalize_embeddings=True)[0]
            scores = self.matrix @ q          # 归一化向量点积 = 余弦相似度
        else:
            q = self.vectorizer.transform([query])
            scores = cosine_similarity(q, self.matrix)[0]

        order = np.argsort(-scores)[:top_k]
        return [{**self.docs[i], "score": float(scores[i])} for i in order]


# ==================== 查询改写 ====================
REWRITE_PROMPT = """把玩家的问题翻译成《英雄联盟》游戏里的术语。

只输出关键词，用空格分隔。不要解释，不要标点，不要换行。

例子：
  玩家问：能回血的符文
  改写：生命偷取 全能吸血 治疗 回复

  玩家问：加攻速的
  改写：攻击速度 攻速

  玩家问：有啥能让我放技能快点的
  改写：技能急速 冷却缩减"""


def rewrite_query(query: str) -> str:
    """
    用大模型把口语问题改写成「描述里可能出现的术语」。

    为什么要这一步（实测数据）：
        检索命中率按提问方式差别很大 ——
        问「加法术强度」（和描述用词一致）→ 5/5 命中
        问「能回血」（口语）           → 0~2/5 命中

        描述里写的是「生命偷取」「全能吸血」，
        玩家嘴里说的是「回血」。词对不上，向量再强也对不齐。

    这是 RAG 里的标准动作，叫 query rewriting。
    代价是每次检索多一次 API 调用 —— 所以只在你觉得必要时开。
    """
    api_key = os.environ.get("MIMO_API_KEY")
    if not api_key:
        return query          # 没 key 就原样返回，不阻断检索

    try:
        client = OpenAI(api_key=api_key, base_url=BASE_URL)
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": REWRITE_PROMPT},
                {"role": "user", "content": f"玩家问：{query}\n改写："},
            ],
            max_completion_tokens=2000,
        )
        out = (resp.choices[0].message.content or "").strip()
        return out if out else query
    except Exception as e:
        print(f"  （改写失败，用原问题检索：{type(e).__name__}）")
        return query


# ==================== 增强 + 生成 ====================
SYSTEM_PROMPT = """你在帮《英雄联盟》海克斯大乱斗的玩家查符文。

规则：
- **只能根据下面给出的资料回答**，资料里没写的不要编。
- 如果资料里找不到答案，就直接说「找到的符文里没有符合的」，不要硬凑。
- 提到符文时用它的中文名，可以顺带说一句它的效果。
- 控制在 150 字以内。"""


def answer(query: str, hits: list[dict]) -> str:
    api_key = os.environ.get("MIMO_API_KEY")
    if not api_key:
        raise RuntimeError("没读到 MIMO_API_KEY，先设环境变量再重开终端")

    # 「增强」就是这一步：把检索到的资料拼进提示词。
    # 模型本身不知道这些符文，是我们现喂给它的。
    context = "\n".join(f"- {h['name']}：{h['text']}" for h in hits)

    prompt = f"""资料：
{context}

玩家的问题：{query}

请根据上面的资料回答。"""

    client = OpenAI(api_key=api_key, base_url=BASE_URL)
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        max_completion_tokens=2000,
    )
    text = (resp.choices[0].message.content or "").strip()
    if not text:
        raise RuntimeError("模型返回了空内容，重跑一次试试")
    return text


# ==================== 主流程 ====================
def main():
    args = [a for a in sys.argv[1:] if a != "--tfidf"]
    use_tfidf = "--tfidf" in sys.argv

    if not args:
        print('用法：python rag.py "你的问题" [--tfidf]')
        print('      python rag.py "哪些符文能回血"')
        print('      python rag.py "加攻速的符文" --tfidf   # 对比字面匹配')
        return 1

    query = args[0]
    mode = "tfidf" if use_tfidf else "semantic"
    no_rewrite = "--no-rewrite" in sys.argv

    print("=" * 62)
    print(f"问题：{query}")
    print(f"检索方式：{'TF-IDF（字面匹配）' if use_tfidf else '语义向量（懂意思）'}")
    print("=" * 62)

    # 口语问题先改写成术语，用改写后的问题去检索（原文还是给模型看）
    search_query = query
    if not no_rewrite and not use_tfidf:
        rewritten = rewrite_query(query)
        if rewritten and rewritten != query:
            print(f"\n查询改写：{query}  →  {rewritten}")
            search_query = rewritten

    index = Index(mode=mode)
    print(f"\n文档库：{len(index.docs)} 个符文")
    print(f"向量维度：{index.matrix.shape[1]:,}")

    hits = index.search(search_query, TOP_K)

    print(f"\n--- 检索到的 {len(hits)} 条（RAG 的 R）---")
    for i, h in enumerate(hits, 1):
        print(f"  {i}. [{h['score']:.4f}] {h['name']}")
        print(f"         {h['text'][:58]}...")

    print(f"\n--- 模型基于以上资料的回答（RAG 的 G）---")
    print(answer(query, hits))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
