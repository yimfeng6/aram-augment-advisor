"""符文助手 —— 用自然语言问任何问题，模型自己决定去查什么。

和前面几个模块的区别
    rag.py      流程是**写死**的：改写 → 检索 → 生成
    agent.py    流程**由模型决定**：它先想「我该查什么」，
                再决定调哪个工具、传什么参数、要不要接着查下一个

    这就是 Agent 和普通 LLM 应用的分界线：
        普通应用：你规定好步骤，模型只负责其中一步
        Agent  ：你只给工具，模型自己编排步骤

────────────────────────────────────────────────────────
Agent Loop（核心就这几行）
────────────────────────────────────────────────────────

    while 还没结束:
        resp = 调模型(messages, tools)
        if 模型没有要调工具:
            return 模型的话        ← 结束
        执行模型要的工具
        把结果塞回 messages        ← 继续循环

    模型可能连着调好几个工具，也可能一个问题都不调直接回答。
    你控制不了它调几次 —— 所以**必须设上限**，不然可能死循环。

────────────────────────────────────────────────────────
一次真实的执行长这样
────────────────────────────────────────────────────────

    你问：亚索配利刃华尔兹强吗？
      ① 模型说要调 search_champion("亚索")
      ② 我们执行，回传：亚索的基线胜率 57.5%
      ③ 模型说还要调 get_pair_stats("亚索", "利刃华尔兹")
      ④ 我们执行，回传：胜率 49.9%，比基线低 7.6%
      ⑤ 模型不调工具了，输出答案

    中间那几次调用**你没写死**，是模型自己决定的。

用法
    python agent.py "亚索配利刃华尔兹强吗"
    python agent.py "哪些符文能回血"
    python agent.py                       # 交互模式
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

from openai import OpenAI

import rag

DB_PATH = Path("aramkit.db")
BASE_URL = os.environ.get("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1")
MODEL = os.environ.get("MIMO_MODEL", "mimo-v2.6-pro")

MAX_STEPS = 6      # 最多让模型调几轮工具。不设上限可能死循环。
MIN_SAMPLE = 1000


# ==================== 名称查找（模型给的是名字，数据库用的是 id） ====================
def find_champion(name: str):
    """按中文名 / 称号 / slug / 英文名找英雄，返回 (id, 中文名, 基线胜率)。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            """SELECT id, name, win_rate FROM champions
               WHERE name = ? OR title = ? OR slug = ? OR riot_id = ?
                  OR name LIKE ? OR title LIKE ?
               LIMIT 1""",
            (name, name, name.lower(), name, f"%{name}%", f"%{name}%"),
        ).fetchone()
    finally:
        conn.close()
    return row


def find_augment(name: str):
    """按中文名找符文，精确优先、模糊兜底。返回 (id, 中文名)。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT id, name FROM augments WHERE name = ? "
            "OR name LIKE ? ORDER BY LENGTH(name) LIMIT 1",
            (name, f"%{name}%"),
        ).fetchone()
    finally:
        conn.close()
    return row


# ==================== 工具实现 ====================
def tool_search_augments(query: str) -> dict:
    """语义检索符文。复用 rag.py 的索引（模型加载慢，做个缓存）。"""
    global _INDEX
    if "_INDEX" not in globals() or _INDEX is None:
        _INDEX = rag.Index(mode="semantic")
    hits = _INDEX.search(query, 5)
    return {"查询": query, "结果": [
        {"符文": h["name"], "效果": h["text"][:120], "相似度": round(h["score"], 3)}
        for h in hits
    ]}


def tool_get_champion(champion: str) -> dict:
    """查英雄的基线胜率 + 他本赛季最强的几个符文。"""
    ch = find_champion(champion)
    if not ch:
        return {"错误": f"数据库里没有找到英雄「{champion}」"}

    cid, cname, baseline = ch
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            """SELECT a.name, ca.win_rate, ca.delta, ca.sample_count
               FROM champion_augment ca JOIN augments a ON a.id = ca.augment_id
               WHERE ca.champion_id = ? AND ca.sample_count > ?
               ORDER BY ca.win_rate DESC LIMIT 8""",
            (cid, MIN_SAMPLE),
        ).fetchall()
    finally:
        conn.close()

    return {
        "英雄": cname,
        "基线胜率": f"{baseline:.1%}",
        "最强符文": [
            {"符文": r[0], "胜率": f"{r[1]:.1%}", "比基线": f"{r[2]:+.1%}", "样本": r[3]}
            for r in rows
        ],
    }


def tool_get_pair(champion: str, augment: str) -> dict:
    """查「某英雄配某符文」的具体胜率。"""
    ch = find_champion(champion)
    if not ch:
        return {"错误": f"没找到英雄「{champion}」"}
    aug = find_augment(augment)
    if not aug:
        return {"错误": f"没找到符文「{augment}」"}

    cid, cname, baseline = ch
    aid, aname = aug

    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            """SELECT win_rate, delta, sample_count, tier, available_stages
               FROM champion_augment
               WHERE champion_id = ? AND augment_id = ?""",
            (cid, aid),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return {"错误": f"数据里没有「{cname} + {aname}」这个组合",
                "提示": "正常情况下游戏只会给该英雄可用的符文，查不到说明名字可能不对"}

    return {
        "英雄": cname, "符文": aname,
        "该英雄基线胜率": f"{baseline:.1%}",
        "这个组合的胜率": f"{row[0]:.1%}",
        "比基线": f"{row[1]:+.1%}",
        "样本数": row[2],
        "梯度": row[3],
        "可用阶段": row[4],
    }


def tool_get_combo(champion: str, augment_a: str, augment_b: str) -> dict:
    """查两个符文一起选时的组合胜率。"""
    ch = find_champion(champion)
    a = find_augment(augment_a)
    b = find_augment(augment_b)
    if not ch:
        return {"错误": f"没找到英雄「{champion}」"}
    if not a or not b:
        return {"错误": "有一个符文名字没找到"}

    lo, hi = sorted((a[0], b[0]))
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            """SELECT win_rate, delta, sample_count FROM champion_augment_combo
               WHERE champion_id = ? AND augment_a = ? AND augment_b = ?""",
            (ch[0], lo, hi),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return {"结果": f"「{a[1]} + {b[1]}」不在 {ch[1]} 的热门组合里",
                "说明": "源数据每个英雄只收录前 10 对组合，查不到不代表搭不上"}

    return {
        "英雄": ch[1], "组合": f"{a[1]} + {b[1]}",
        "组合胜率": f"{row[0]:.1%}", "比基线": f"{row[1]:+.1%}", "样本": row[2],
    }


# 名字 → 函数。模型返回的 name 通过这张表找到对应的实现。
DISPATCH = {
    "search_augments": tool_search_augments,
    "get_champion": tool_get_champion,
    "get_pair": tool_get_pair,
    "get_combo": tool_get_combo,
}

_INDEX = None


# ==================== 工具定义（给模型看的说明书） ====================
#
# description 非常重要 —— 模型**只靠这段文字**决定什么时候用这个工具。
# 写得不清楚，它就会该用的时候不用、不该用的时候乱用。
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_augments",
            "description": (
                "按效果描述模糊查找符文。当玩家问「哪些符文能XX」「有没有加XX的符文」时用这个。"
                "传入口语化的描述即可，比如「回血」「加攻速」。返回最相关的 5 个符文。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要查的效果，例如：回血、加攻速"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_champion",
            "description": (
                "查某个英雄本赛季的基线胜率，以及他最强的几个符文。"
                "当玩家问「XX该选什么符文」「XX怎么玩」时用这个。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "champion": {"type": "string", "description": "英雄名，例如：亚索、无极剑圣"},
                },
                "required": ["champion"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pair",
            "description": (
                "查「某个英雄配某个符文」的具体胜率，以及它比该英雄平均水平高多少。"
                "当玩家问「XX配YY强吗」「XX选YY好不好」时用这个。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "champion": {"type": "string", "description": "英雄名"},
                    "augment": {"type": "string", "description": "符文名"},
                },
                "required": ["champion", "augment"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_combo",
            "description": (
                "查两个符文一起选时的组合胜率。"
                "当玩家问「A 和 B 一起选怎么样」「这两个搭不搭」时用这个。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "champion": {"type": "string", "description": "英雄名"},
                    "augment_a": {"type": "string", "description": "第一个符文名"},
                    "augment_b": {"type": "string", "description": "第二个符文名"},
                },
                "required": ["champion", "augment_a", "augment_b"],
            },
        },
    },
]

SYSTEM_PROMPT = """你在帮《英雄联盟》海克斯大乱斗的玩家解答问题。

你可以调用几个工具去查真实数据 —— **需要数据就去查，不要凭记忆编**。
查到什么就说什么，查不到就直说查不到。

回答要简洁、口语化，150 字以内。提数字的时候说清楚它是什么意思
（比如「比这个英雄的平均水平高 5%」而不是光甩一个 58%）。"""


# ==================== Agent Loop ====================
def run(question: str, verbose: bool = True) -> str:
    api_key = os.environ.get("MIMO_API_KEY")
    if not api_key:
        raise RuntimeError("没读到 MIMO_API_KEY")

    client = OpenAI(api_key=api_key, base_url=BASE_URL)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]

    for step in range(1, MAX_STEPS + 1):
        resp = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            max_completion_tokens=2000,
        )
        msg = resp.choices[0].message

        # 模型不再要求调工具 = 它准备给最终答案了
        if not msg.tool_calls:
            if verbose:
                print(f"（共 {step - 1} 轮工具调用）\n")
            return (msg.content or "").strip()

        # ---- 模型要求调工具 ----
        #
        # 注意：这时候 msg.content 是空的，是正常的。
        # 模型的整条消息（含 tool_calls）要原样塞回 messages，
        # 否则下一轮它会看不懂上下文。
        messages.append(msg)

        for call in msg.tool_calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            if verbose:
                print(f"  [第 {step} 轮] 模型决定调用 {name}({args})")

            fn = DISPATCH.get(name)
            if fn is None:
                result = {"错误": f"没有这个工具：{name}"}
            else:
                try:
                    result = fn(**args)
                except TypeError as e:
                    result = {"错误": f"参数不对：{e}"}
                except Exception as e:
                    result = {"错误": f"{type(e).__name__}: {e}"}

            if verbose:
                preview = json.dumps(result, ensure_ascii=False)
                print(f"           → {preview[:100]}")

            # 工具结果必须以 role="tool" 回传，并带上对应的 tool_call_id
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result, ensure_ascii=False),
            })

    return f"（模型调了 {MAX_STEPS} 轮工具还没给出答案，停下来了 —— 换个问法试试）"


# ==================== 主流程 ====================
def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]

    if args:
        print("=" * 62)
        print(f"问题：{args[0]}")
        print("=" * 62 + "\n")
        print(run(args[0]))
        return 0

    # 没给问题就进交互模式
    print("符文助手（Ctrl+C 退出）")
    print("直接问，比如：亚索配利刃华尔兹强吗 / 哪些符文能回血\n")
    while True:
        try:
            q = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q:
            continue
        print()
        print(run(q))
        print()


if __name__ == "__main__":
    raise SystemExit(main())
