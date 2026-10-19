"""Web 界面：把「选符文 / 自然语言查 / 问 Agent」做成能点的页面。

启动：
    python web.py
然后浏览器打开 http://127.0.0.1:8000

设计上这一层只做薄壳，不复制任何业务逻辑：

    /api/advise  →  advise.advise()      查库、排序（纯本地，不用 API key）
    /api/rag     →  rag.Index / rag.answer  语义检索 + 生成
    /api/agent   →  agent.run()          Agent Loop

命令行怎么跑，网页就是怎么跑。改一处，两边同时生效。

为什么值得多这一层：
    前面几个模块都是命令行，只能看到「结果」。
    Agent 最好看的部分是**过程** —— 模型在第几轮决定了调哪个工具、
    传了什么参数、拿到什么结果。命令行里这些是滚过去的 print，
    网页上可以把它一条条摊开。
"""

import os
import sqlite3
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent

# 项目里所有模块都用相对路径找 aramkit.db。
# 从别的目录启动时（比如 IDE 的默认工作目录），统一切到项目根目录。
os.chdir(ROOT)

import advise  # noqa: E402
import agent  # noqa: E402
import rag  # noqa: E402

app = FastAPI(title="符文助手", version="1.0")


# ==================== 索引缓存 ====================
#
# bge 模型第一次加载要几秒，不能每个请求都重新建索引。
# 加锁是为了挡住并发的首批请求 —— 否则会同时加载两份模型。
_indexes: dict = {}
_index_lock = threading.Lock()


def get_index(mode: str) -> "rag.Index":
    with _index_lock:
        if mode not in _indexes:
            _indexes[mode] = rag.Index(mode=mode)
        return _indexes[mode]


# ==================== 请求体 ====================
class AdviseReq(BaseModel):
    champion_slug: str
    augment_ids: list[int]
    level: int | None = None
    picked: list[int] = []


class RagReq(BaseModel):
    query: str
    mode: str = "semantic"      # semantic | tfidf
    rewrite: bool = True


class AgentReq(BaseModel):
    question: str


# ==================== 接口 ====================
@app.get("/api/bootstrap")
def bootstrap():
    """一次把下拉框要用的数据全发给前端。

    173 个英雄 + 216 个符文，加起来不到 100 KB ——
    比每输入一个字就请求一次后端简单得多。
    """
    conn = sqlite3.connect(advise.DB_PATH)
    try:
        champions = [
            {"id": r[0], "name": r[1], "title": r[2], "slug": r[3],
             "win_rate": r[4], "tier": r[5], "sample_count": r[6], "roles": r[7]}
            for r in conn.execute(
                "SELECT id, name, title, slug, win_rate, tier, sample_count, roles "
                "FROM champions ORDER BY name")
        ]
        augments = [
            {"id": r[0], "name": r[1], "rarity": r[2], "description": r[3]}
            for r in conn.execute(
                "SELECT id, name, rarity, description FROM augments ORDER BY id")
        ]
    finally:
        conn.close()

    return {
        "champions": champions,
        "augments": augments,
        "llm_available": bool(os.environ.get("MIMO_API_KEY")),
        "model": rag.MODEL,
    }


@app.post("/api/advise")
def api_advise(req: AdviseReq):
    """给定英雄 + 候选符文，返回排序后的建议。不调用大模型。"""
    if not req.augment_ids:
        raise HTTPException(400, "至少选一个符文")
    if len(req.augment_ids) > 3:
        raise HTTPException(400, "海克斯一次最多三个选项")
    if req.level is not None and not 1 <= req.level <= 18:
        raise HTTPException(400, "等级要在 1~18 之间")

    stage = advise.stage_from_level(req.level)
    champion_name, suggestions = advise.advise(
        req.champion_slug, req.augment_ids,
        stage=stage, picked=req.picked or None,
    )
    if champion_name is None:
        raise HTTPException(404, suggestions)     # 第二个返回值是错误字符串

    row = advise.lookup_champion(req.champion_slug)

    out = []
    for s in suggestions:
        combo = None
        if s.combo:
            combo = {
                "partner": advise.augment_name(s.combo[0]),
                "win_rate": s.combo[1],
                "delta": s.combo[2],
                "sample_count": s.combo[3],
            }
        out.append({
            "id": s.augment_id,
            "name": s.name,
            "win_rate": s.win_rate,
            "global_win_rate": s.global_win_rate,
            "delta": s.delta,
            "sample_count": s.sample_count,
            "tier": s.tier,
            "stages": s.stages,
            "notes": s.notes,
            "combo": combo,
        })

    return {
        "champion": champion_name,
        "champion_title": row[2] if row else None,
        "baseline": row[3] if row else None,
        "level": req.level,
        "stage": stage,
        "suggestions": out,
        "text": advise.format_advice(champion_name, suggestions, req.level),
    }


@app.post("/api/rag")
def api_rag(req: RagReq):
    """自然语言查符文：改写 → 检索 → 生成。"""
    query = req.query.strip()
    if not query:
        raise HTTPException(400, "问题不能为空")
    if req.mode not in ("semantic", "tfidf"):
        raise HTTPException(400, "mode 只能是 semantic 或 tfidf")

    # 口语问题先改写成游戏术语，再去检索（和 rag.py 主流程一致）
    search_query = query
    rewritten = None
    if req.rewrite and req.mode == "semantic":
        rewritten = rag.rewrite_query(query)
        if rewritten and rewritten != query:
            search_query = rewritten

    index = get_index(req.mode)
    hits = index.search(search_query, rag.TOP_K)

    answer_text = None
    answer_error = None
    if os.environ.get("MIMO_API_KEY"):
        try:
            answer_text = rag.answer(query, hits)
        except Exception as e:                       # 模型出错不该拖垮检索结果
            answer_error = f"{type(e).__name__}: {e}"
    else:
        answer_error = "没设 MIMO_API_KEY，只返回检索结果，不生成回答"

    return {
        "query": query,
        "search_query": search_query,
        "rewritten": rewritten,
        "mode": req.mode,
        "doc_count": len(index.docs),
        "vector_dim": int(index.matrix.shape[1]),
        "hits": hits,
        "answer": answer_text,
        "answer_error": answer_error,
    }


@app.post("/api/agent")
def api_agent(req: AgentReq):
    """Agent 问答。返回 answer + 每一次工具调用的记录。"""
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "问题不能为空")
    if not os.environ.get("MIMO_API_KEY"):
        raise HTTPException(400, "没设 MIMO_API_KEY，Agent 用不了")

    trace: list = []
    try:
        text = agent.run(question, verbose=False, trace=trace)
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")

    return {
        "question": question,
        "answer": text,
        "trace": trace,
        "tool_calls": len(trace),
        "max_steps": agent.MAX_STEPS,
    }


# ==================== 静态页面 ====================
app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")


@app.get("/")
def home():
    return FileResponse(ROOT / "web" / "index.html")


if __name__ == "__main__":
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="符文助手 Web 界面")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    if args.open:
        import webbrowser
        # 等 uvicorn 把端口监听起来再开浏览器，否则会先看到一个「无法访问」
        threading.Timer(2.0, lambda: webbrowser.open(url)).start()

    print(f"\n  符文助手  →  {url}\n")
    uvicorn.run(app, host=args.host, port=args.port)
