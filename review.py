"""赛后复盘：把这一局的符文选择交给大模型，生成一段自然语言分析。

和 advise.py 的分工
    advise.py  选**之前**用 —— 告诉你三个候选里选哪个
    review.py  选**之后**用 —— 告诉你这一局选得怎么样、下次注意什么

数据流
    picked.txt（你选过的符文 id）+ 英雄
      → 查库：每个选择的实际表现（胜率、收益率、样本量）
      → 查库：这个英雄本赛季最强的几个符文（作为参照）
      → 组装成结构化事实 → 交给大模型 → 输出一段人话

────────────────────────────────────────────────────────
最重要的一个设计原则：别让大模型做算术
────────────────────────────────────────────────────────

胜率、收益率、样本量，全部由 Python 从数据库查好再喂进去。
模型只负责「把数字翻译成人话」。

为什么？
    LLM 做算术不可靠 —— 它可能把 55.3 - 53.2 算成 1.1 或者 3.2，
    而且你很难发现。
    但让它组织语言、给判断，它非常擅长。

    所以正确的分工是：
        程序负责**事实**，模型负责**表达**。

    这条原则适用于所有 LLM 应用 —— 数字、时间、排序、统计，
    一律程序算完再给模型。

────────────────────────────────────────────────────────
用法
    python review.py                          # 读 picked.txt
    python review.py masteryi                 # 指定英雄
    python review.py masteryi 1047,1328,1092  # 指定英雄和符文 id

需要环境变量 MIMO_API_KEY
"""

import os
import sqlite3
import sys
from pathlib import Path

from openai import OpenAI

import advise

DB_PATH = Path("aramkit.db")
PICKED_FILE = Path("picked.txt")

# MiMo 的 OpenAI 兼容端点；密钥类型是 sk- 开头时用这个
BASE_URL = os.environ.get("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1")
MODEL = os.environ.get("MIMO_MODEL", "mimo-v2.6-pro")


# ==================== 第一步：从数据库收集事实 ====================
def gather_facts(champion_slug: str, picked_ids: list) -> dict | None:
    """
    把这局需要的事实全部查出来。

    注意这里**没有任何模型参与** —— 全是 SQL 查询。
    模型拿到的是已经算好的结论，不是原始数据。
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        champ = conn.execute(
            "SELECT id, name, win_rate, sample_count FROM champions WHERE slug = ?",
            (champion_slug.lower(),),
        ).fetchone()
        if champ is None:
            return None

        cid, cname, baseline, csample = champ

        # 这一局选过的每个符文，在该英雄身上的表现
        picks = []
        for aid in picked_ids:
            row = conn.execute(
                """
                SELECT a.name, ca.win_rate, ca.delta, ca.sample_count, ca.tier
                FROM champion_augment ca
                JOIN augments a ON a.id = ca.augment_id
                WHERE ca.champion_id = ? AND ca.augment_id = ?
                """,
                (cid, aid),
            ).fetchone()
            if row:
                picks.append({
                    "name": row[0], "win_rate": row[1], "delta": row[2],
                    "sample": row[3], "tier": row[4],
                })

        # 该英雄本赛季表现最好的几个符文，作为"本来可以选"的参照
        best = conn.execute(
            """
            SELECT a.name, ca.win_rate, ca.delta, ca.sample_count
            FROM champion_augment ca
            JOIN augments a ON a.id = ca.augment_id
            WHERE ca.champion_id = ? AND ca.sample_count > ?
            ORDER BY ca.win_rate DESC LIMIT 5
            """,
            (cid, advise.MIN_SAMPLE),
        ).fetchall()
    finally:
        conn.close()

    if not picks:
        return None

    # 这一局里最好/最差的pick —— 也是程序算的
    ranked = sorted(picks, key=lambda p: -(p["delta"] or -9))
    facts = {
        "champion": cname,
        "baseline": baseline,
        "champion_sample": csample,
        "picks": picks,
        "best_possible": [
            {"name": b[0], "win_rate": b[1], "delta": b[2], "sample": b[3]}
            for b in best
        ],
        "best_pick": ranked[0],
        "worst_pick": ranked[-1],
        "avg_delta": sum(p["delta"] or 0 for p in picks) / len(picks),
    }
    return facts


# ==================== 第二步：把事实组装成提示词 ====================
SYSTEM_PROMPT = """你是一个《英雄联盟》海克斯大乱斗的老玩家，正在帮朋友复盘他刚打完的一局。

说话直接、口语化，像一起看录像时随口点评那样。

要求：
- **不要复述数字**。数据已经在下面了，我要的是你的判断，不是念数据。
- 该夸就夸，该说"这个选亏了"就直说，别和稀泥。
- 150 字以内，不要分点罗列，写成一段连贯的话。"""


def build_prompt(facts: dict) -> str:
    lines = [
        f"英雄：{facts['champion']}",
        f"他本赛季平均胜率：{facts['baseline']:.1%}",
        "",
        "这一局依次选的是：",
    ]
    for i, p in enumerate(facts["picks"], 1):
        lines.append(
            f"  {i}. {p['name']}  胜率 {p['win_rate']:.1%}  "
            f"比该英雄平均 {p['delta']:+.1%}  样本 {p['sample']:,}"
        )

    lines.append("")
    lines.append(f"（对比）{facts['champion']}本赛季胜率最高的几个符文：")
    for i, b in enumerate(facts["best_possible"], 1):
        lines.append(f"  {i}. {b['name']}  胜率 {b['win_rate']:.1%}  比平均 {b['delta']:+.1%}")

    lines += [
        "",
        "请说清楚三件事：",
        "1. 这一局整体选得怎么样",
        "2. 哪一个选得好、哪一个下次该避开",
        "3. 之后再玩这个英雄要注意什么",
    ]
    return "\n".join(lines)


# ==================== 第三步：调模型 ====================
def ask_model(prompt: str, system: str = SYSTEM_PROMPT, retries: int = 2) -> str:
    """
    调模型。返回正文（不含思考过程）。

    这里踩过两个坑，都值得记下来：

    【坑一】max_completion_tokens 给太小
        MiMo 这类模型会先「思考」再回答，思考也占 token。
        上限太小的话，思考把额度用光，正文就是空的 ——
        而 finish_reason 可能是 'length'，也可能看起来正常。
        所以给 2000，留够余量。

    【坑二】HTTP 200 不等于拿到了内容
        实测遇到过一次：请求成功、状态码 200、finish_reason='stop'，
        但 content 是空字符串。**不报错，就是空的。**

        所以下面显式检查内容长度，空了就重试，
        重试还不行就抛错 —— 不要让一个空字符串悄悄往下传。

        （这个项目里已经栽过好几次「静默失败」了：数据造假、
          识别输出 class_916、视频混进仓库…… 都是同一个病。）
    """
    api_key = os.environ.get("MIMO_API_KEY")
    if not api_key:
        raise RuntimeError(
            "没读到环境变量 MIMO_API_KEY。\n"
            "  设置方法：setx MIMO_API_KEY \"sk-你的key\"，然后重开终端"
        )

    client = OpenAI(api_key=api_key, base_url=BASE_URL)
    last_reason = ""

    for attempt in range(1, retries + 2):
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            max_completion_tokens=2000,      # 见坑一：要给思考留余量
        )
        choice = resp.choices[0]
        text = (choice.message.content or "").strip()
        last_reason = choice.finish_reason

        if text:
            return text

        print(f"  ⚠ 第 {attempt} 次返回空内容（finish_reason={last_reason}），重试")

    raise RuntimeError(
        f"模型连续 {retries + 1} 次返回空内容，最后一次 finish_reason={last_reason}。\n"
        f"  可能是 token 上限不够，或者模型临时抽风。"
    )


# ==================== 主流程 ====================
def read_picked() -> list:
    """从 picked.txt 读已选的符文 id（和 watch.py 共用同一个文件）。"""
    if not PICKED_FILE.exists():
        return []
    text = PICKED_FILE.read_text(encoding="utf-8")
    out = []
    for tok in text.replace(",", " ").replace("，", " ").split():
        if tok.strip().isdigit():
            out.append(int(tok.strip()))
    return out


def review(champion_slug: str, picked_ids: list) -> str:
    facts = gather_facts(champion_slug, picked_ids)
    if facts is None:
        return f"查不到数据：英雄 slug={champion_slug!r}，或者这几个符文不在他的可用列表里"

    prompt = build_prompt(facts)
    body = ask_model(prompt)

    # 复盘的正文由模型写，但"数据摘要"由程序生成 —— 各司其职
    head = (
        f"【复盘】{facts['champion']} · 本赛季平均胜率 {facts['baseline']:.1%}\n"
        f"  最好的一选：{facts['best_pick']['name']} ({facts['best_pick']['delta']:+.1%})\n"
        f"  最亏的一选：{facts['worst_pick']['name']} ({facts['worst_pick']['delta']:+.1%})\n"
        f"  平均收益：{facts['avg_delta']:+.1%}\n"
    )
    return head + "\n" + body


def save_review(champion_name: str, text: str, path: str = "reviews.md") -> None:
    """把一次复盘追加到 reviews.md。"""
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")

    # 用 markdown 的二级标题分节，每次复盘一段
    block = f"## {today} {champion_name}\n\n{text}\n\n---\n\n"

    # mode="a" 是追加；用 "w" 会把之前的内容全冲掉
    # encoding 必须显式写，否则 Windows 下中文乱码（你栽过一次）
    with open(path, "a", encoding="utf-8") as f:
        f.write(block)

    print(f"\n（已追加到 {path}）")


def main():
    args = sys.argv[1:]

    save = "--save" in args
    if save:
        args.remove("--save")

    champion = args[0] if args else os.environ.get("CHAMPION_SLUG", "")
    if not champion:
        print("用法：python review.py <英雄slug> [符文id,符文id,...]")
        print("      python review.py masteryi 1047,1328,1092")
        print("      python review.py masteryi 1047 --save")
        print("      （不给符文就用 picked.txt 里的）")
        return 1

    if len(args) > 1:
        picked = [int(x) for x in args[1].replace("，", ",").split(",") if x.strip().isdigit()]
    else:
        picked = read_picked()

    if not picked:
        print("没有已选的符文。用 picked.txt 记一下，或者在命令行里给 id。")
        return 1

    print(f"英雄：{champion}    已选符文 id：{picked}\n")
    result = review(champion, picked)    # 先接住返回值
    print(result)


    if save:
        info = advise.lookup_champion(champion)  # 传 slug 进去
        name = info[1] if info else champion  # 取中文名；查不到就用 slug 兜底
        save_review(name, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

