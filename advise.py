"""步骤 5：给定英雄和三个候选符文，给出选择建议。

这是整条链路的最后一步，也是唯一一步需要"下判断"的地方：

    前面几步都只负责"搞对数据"：
        liveapi  → 你在玩谁、几级
        recognize → 屏幕上那三个是什么
        aramkit.db → 它们的胜率是多少

    到了这里才有选择：按什么排序？样本少怎么办？几个信号冲突听谁的？

关于排序指标（更正过一次）：

    我一开始说"按胜率排"和"按收益率排"会给出不同顺序，并举了例子。
    那个例子是错的 —— 因为当时 delta 减错了基准（减的是符文的全局胜率，
    而不是该英雄自己的基线）。

    改用正确基准后：

        delta = win_rate - 该英雄自己的基线胜率

    在同一个英雄内部，基线是常数，所以
        delta(A) > delta(B)  ⟺  win_rate(A) > win_rate(B)
    两种排序**完全等价**。

    区别只在跨英雄比较时才出现（比如"哪个符文整体最强"）。
    对我们的场景（同一个英雄的三个候选里挑一个），两种指标是同一件事。
    所以 SORT_KEY 保留着，但换成哪个结果都一样。

关于样本量：
    有些「英雄 × 符文」组合只有几十场数据，胜率完全不可信。
    低于 MIN_SAMPLE 的会标出来，但不会自动剔除 ——
    因为冷门组合本来就没数据，直接扔掉等于没建议。
"""

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

DB_PATH = Path("aramkit.db")

SORT_KEY = "win_rate"       # "win_rate" 或 "delta"
MIN_SAMPLE = 1000           # 低于这个样本数就标注"样本少"


@dataclass
class Suggestion:
    """一个候选符文的建议数据。"""
    augment_id: int
    name: str
    win_rate: float | None = None          # 该英雄选该符文的胜率
    global_win_rate: float | None = None   # 该符文的全局胜率（展示用）
    baseline: float | None = None          # 该英雄自己的基线胜率
    delta: float | None = None             # 收益率 = win_rate - baseline
    sample_count: int | None = None
    tier: str | None = None
    stages: str = ""
    notes: list = field(default_factory=list)
    # 和「已选符文」的组合情况：(搭档 id, 组合胜率, 组合收益, 样本数)
    combo: tuple | None = None


def lookup_champion(slug: str):
    """英文 slug → (id, 中文名, 称号, 基线胜率)。找不到返回 None。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        return conn.execute(
            "SELECT id, name, title, win_rate FROM champions WHERE slug = ?",
            (slug.lower(),),
        ).fetchone()
    finally:
        conn.close()


def _combo_lookup(conn, champion_id: int, candidates: list, picked: list) -> dict:
    """查每个候选和「已选符文」的组合表现。

    返回 {候选 id: (搭档 id, 组合胜率, 组合收益, 样本数)}。

    注意源数据每个英雄只给前 10 对组合，所以这里的覆盖是不全的 ——
    查不到不代表搭不上，只代表"不在该英雄最热门的 10 对里"。
    """
    if not picked:
        return {}

    out = {}
    for aid in candidates:
        best = None
        for p in picked:
            a, b = sorted((aid, p))
            row = conn.execute(
                "SELECT win_rate, delta, sample_count FROM champion_augment_combo "
                "WHERE champion_id = ? AND augment_a = ? AND augment_b = ?",
                (champion_id, a, b),
            ).fetchone()
            if (row and row[0] is not None
                    and (best is None or (row[1] or 0) > (best[2] or 0))):
                best = (p, row[0], row[1], row[2])
        if best:
            out[aid] = best
    return out


def advise(champion_slug: str, augment_ids: list, stage: int | None = None,
           picked: list | None = None):
    """查出这三个符文在该英雄身上的表现，按胜率排序。

    返回 (英雄名, [Suggestion...])；英雄找不到时返回 (None, 错误字符串)。
    """
    champion = lookup_champion(champion_slug)
    if champion is None:
        return None, f"数据库里没有 slug = {champion_slug!r} 的英雄"
    champion_id, champion_name, _title, baseline = champion

    if not augment_ids:
        return champion_name, []

    conn = sqlite3.connect(DB_PATH)
    try:
        placeholders = ",".join("?" * len(augment_ids))
        rows = conn.execute(
            f"""
            SELECT a.id, a.name, ca.win_rate, ca.augment_win_rate, ca.delta,
                   ca.sample_count, ca.tier, ca.available_stages
            FROM champion_augment ca
            JOIN augments a ON a.id = ca.augment_id
            WHERE ca.champion_id = ? AND ca.augment_id IN ({placeholders})
            """,
            (champion_id, *augment_ids),
        ).fetchall()
    finally:
        conn.close()

    by_id = {r[0]: r for r in rows}

    suggestions = []
    for aid in augment_ids:
        if aid is None:
            suggestions.append(Suggestion(0, "（没认出来）", notes=["识别失败"]))
            continue

        r = by_id.get(aid)
        if r is None:
            # 数据里没有"这个英雄 + 这个符文"的组合。
            # 正常情况下不该发生（游戏只给该英雄可用的符文），
            # 所以出现就说明识别可能错了。
            suggestions.append(Suggestion(
                aid, "（无该英雄的该符文数据）", notes=["可能是识别错了"]))
            continue

        s = Suggestion(
            augment_id=r[0], name=r[1], win_rate=r[2], global_win_rate=r[3],
            baseline=baseline, delta=r[4],
            sample_count=r[5], tier=r[6], stages=r[7] or "",
        )

        if s.sample_count is not None and s.sample_count < MIN_SAMPLE:
            s.notes.append(f"样本仅 {s.sample_count:,}，胜率不太可信")

        # 交叉验证：这个阶段本来不该出现这个符文 → 识别大概率错了
        if stage and s.stages:
            avail = [x.strip() for x in s.stages.split(",")]
            if str(stage) not in avail:
                s.notes.append(f"该符文只在第 {'/'.join(avail)} 阶段出现，与当前第 {stage} 阶段不符")

        suggestions.append(s)

    # ---- 组合：如果知道已经选过哪些符文，把「和它们搭得上」的往前提 ----
    #
    # 排序规则：
    #   1. 和已选符文是「已知高胜率组合」的排前面
    #   2. 同组内按胜率排
    #
    # 为什么不把组合胜率直接混进总排序：组合胜率是两个符文一起选时的数字，
    # 和单个符文的胜率不是一个量纲，硬加在一起没有依据。
    # 所以只用它做「有没有已知好搭档」这个粗判断，明细照实显示，由人来定。
    combo_hits = {}
    if picked:
        conn = sqlite3.connect(DB_PATH)
        try:
            cid = conn.execute("SELECT id FROM champions WHERE slug = ?",
                               (champion_slug.lower(),)).fetchone()[0]
            ids = [s.augment_id for s in suggestions if s.augment_id]
            combo_hits = _combo_lookup(conn, cid, ids, picked)
        finally:
            conn.close()

    for s in suggestions:
        if s.augment_id in combo_hits:
            s.combo = combo_hits[s.augment_id]

    key = (lambda s: s.delta) if SORT_KEY == "delta" else (lambda s: s.win_rate)
    suggestions.sort(key=lambda s: (
        s.augment_id not in combo_hits,      # 有已知好搭档的排前面
        key(s) is None,
        -(key(s) or 0),
    ))

    return champion_name, suggestions


def format_advice(champion_name: str, suggestions: list, level: int | None = None) -> str:
    """把建议排成给人看的文本。"""
    head = champion_name
    if level is not None:
        head += f" · {level} 级"

    base = next((s.baseline for s in suggestions if s.baseline is not None), None)
    if base is not None:
        head += f"   （他本赛季平均胜率 {base:.1%}）"

    lines = [f"[推荐] {head}"]

    # 三个都低于该英雄自己的平均线时点一句。
    # 注意这不算罕见 —— 三个随机选项全抽到下风的概率大约 1/8，
    # 所以这话只是提醒，不是"出大事了"。
    deltas = [s.delta for s in suggestions if s.delta is not None]
    if deltas and all(d < 0 for d in deltas):
        lines.append("  ⚠ 这三个都低于该英雄的平均水平")

    for i, s in enumerate(suggestions, 1):
        if s.win_rate is None:
            lines.append(f"  {i}. {s.name}")
        else:
            delta = f"{s.delta:+.1%}" if s.delta is not None else "  ?  "
            lines.append(
                f"  {i}. {s.name:<16} 胜率 {s.win_rate:.1%}"
                f"   比平均 {delta}   （符文全局 {s.global_win_rate:.1%}）"
            )
        if s.combo:
            partner_id, cwr, cdelta, csc = s.combo
            partner = augment_name(partner_id)
            lines.append(f"       ⚑ 和已选的「{partner}」是热门组合："
                         f"组合胜率 {cwr:.1%}（样本 {csc:,}）")
        for n in s.notes:
            lines.append(f"       ⚠ {n}")

    return "\n".join(lines)


def augment_name(augment_id: int) -> str:
    """按 id 查符文名。只用于显示组合的搭档是谁。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("SELECT name FROM augments WHERE id = ?",
                           (augment_id,)).fetchone()
        return row[0] if row else f"id={augment_id}"
    finally:
        conn.close()


def stage_from_level(level: int | None) -> int | None:
    """等级 → 海克斯阶段（开局 / 7 / 11 / 15 级对应 1~4）。"""
    if level is None:
        return None
    if level >= 15:
        return 4
    if level >= 11:
        return 3
    if level >= 7:
        return 2
    return 1


if __name__ == "__main__":
    # 自检：用截图里已验证过的组合跑一遍
    print("=== 自检 1：疾风剑豪（亚索）在 shot_002 那三个符文上的表现 ===")
    name, sug = advise("yasuo", [1170, 1007, 2024], stage=1)
    print(format_advice(name, sug))

    print("\n=== 自检 2：未来守护者（杰斯）在 shot_004 那三个符文上的表现 ===")
    name, sug = advise("jayce", [1], stage=1)   # 先确认英雄能不能查到
    print(f"查询 jayce: {name}")

    print("\n=== 自检 3：查一个不存在的英雄 ===")
    print(advise("notachampion", [1]))
