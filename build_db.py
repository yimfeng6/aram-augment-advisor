"""步骤 2：把 raw/ 里的嵌套 JSON 整理成 SQLite 数据库。

为什么要有这一步？
    步骤 1 存下来的是「网站的数据结构」——为了前端渲染方便，它是嵌套的。
    步骤 5 需要的是「我们的数据结构」——为了查询方便，它应该是平的。

    直接读嵌套 JSON 也能work，但每次查询都要遍历 21,647 条记录。
    建库之后，一次 SQL 查询就能拿到结果。

三张表的设计：
    champions         英雄词表        173 行
    augments          符文词表        216 行
    champion_augment  英雄×符文交叉  21,647 行
    meta              数据来源信息

为什么要拆三张表，不用一张大表？
    一张大表的话，每一行都要重复存一次英雄名和符文名——
    「黑暗之女」这个名字要存 216 遍。
    拆表后每个名字只存一次。这叫「消除冗余」。
    代价是查询时要 JOIN，但这个代价小得多。

运行：
    python build_db.py
"""

import json
import sqlite3
from pathlib import Path

RAW = Path("raw")
DB = Path("aramkit.db")


# ==================== 读原始数据 ====================
def load(name: str):
    return json.loads((RAW / name).read_text(encoding="utf-8"))


def tooltip_text(tip) -> str:
    """把嵌套的 tooltip 结构拍成一段纯文本。

    原始结构是 {blocks: [{spans: [{text: ..., semantic: ...}]}]}，
    为了保留富文本的颜色标记才这么设计。我们只需要文字。
    """
    parts = []
    for block in (tip or {}).get("blocks", []):
        for span in block.get("spans", []):
            parts.append(span.get("text", ""))
    return "".join(parts).strip()


# ==================== 建表 ====================
SCHEMA = """
CREATE TABLE champions (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,  -- 黑暗之女
    title        TEXT,           -- 安妮
    slug         TEXT,           -- annie
    riot_id      TEXT,           -- Annie
    roles        TEXT,           -- 逗号分隔: "Mage,Support"
    icon         TEXT,
    -- 该英雄自己的基线胜率。算「符文对这个英雄的增益」时必须减它，
    -- 不能减符文的全局胜率 —— 那是另一个量，见 champion_augment 的注释。
    win_rate     REAL,
    tier         TEXT,
    sample_count INTEGER
);

CREATE TABLE augments (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,  -- 亮出你的剑
    slug        TEXT,           -- draw-your-sword
    rarity      TEXT,           -- prismatic / gold / silver
    icon        TEXT,
    description TEXT            -- tooltip 拍平后的纯文本
);

CREATE TABLE champion_augment (
    champion_id      INTEGER NOT NULL,
    augment_id       INTEGER NOT NULL,
    win_rate         REAL,      -- 该英雄选该符文的胜率
    augment_win_rate REAL,      -- 该符文的全局胜率（只作展示，不参与算增益）
    delta            REAL,      -- ← 收益率 = win_rate - 该英雄自己的基线胜率
    sample_count     INTEGER,
    pick_rate        REAL,
    rank             INTEGER,
    tier             TEXT,
    available_stages TEXT,      -- 如 "1,2,3,4"

    PRIMARY KEY (champion_id, augment_id),
    FOREIGN KEY (champion_id) REFERENCES champions(id),
    FOREIGN KEY (augment_id)  REFERENCES augments(id)
);

CREATE TABLE champion_augment_combo (
    champion_id INTEGER NOT NULL,
    augment_a   INTEGER NOT NULL,   -- 两个符文 id，约定 a < b 方便去重
    augment_b   INTEGER NOT NULL,
    win_rate    REAL,               -- 这两个一起选时的胜率
    delta       REAL,               -- 减去该英雄基线后的收益
    sample_count INTEGER,
    pick_rate   REAL,
    rank        INTEGER,            -- 该英雄内的排名（源数据只给前 10 对）

    PRIMARY KEY (champion_id, augment_a, augment_b),
    FOREIGN KEY (champion_id) REFERENCES champions(id)
);

-- 查询几乎永远是「给定英雄，看它的所有符文」，所以按英雄建索引
CREATE INDEX idx_ca_champion ON champion_augment(champion_id);
CREATE INDEX idx_cac_champion ON champion_augment_combo(champion_id);

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def create_schema(conn: sqlite3.Connection) -> None:
    # SQLite 默认不检查外键，必须手动打开。
    # 打开之后，插入了词表里不存在的 id 会直接报错——
    # 这正是我们要的：让脏数据在入库时就暴露，而不是等到查询时才发现。
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)


# ==================== 灌数据 ====================
def insert_champions(conn, champions: dict, baselines: dict) -> int:
    rows = []
    for cid, c in champions.items():
        b = baselines.get(int(cid), {})
        rows.append((
            int(cid),
            c.get("name", ""),
            c.get("title"),
            c.get("slug"),
            c.get("riotId"),
            ",".join(c.get("roles") or []),
            c.get("icon"),
            b.get("winRate"),        # 该英雄自己的基线胜率
            b.get("tier"),
            b.get("sampleCount"),
        ))
    conn.executemany(
        "INSERT INTO champions VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def insert_augments(conn, augments: dict) -> int:
    rows = [
        (
            int(aid),
            a.get("name", ""),
            a.get("slug"),
            a.get("rarity"),
            a.get("icon"),
            tooltip_text(a.get("tooltip")),
        )
        for aid, a in augments.items()
    ]
    conn.executemany("INSERT INTO augments VALUES (?,?,?,?,?,?)", rows)
    return len(rows)


def insert_details(conn, detail_dir: Path, baselines: dict) -> int:
    """把 173 个英雄详情拍平成 champion_augment 的行。"""
    rows = []
    for path in sorted(detail_dir.glob("*.json")):
        detail = json.loads(path.read_text(encoding="utf-8"))
        champion_id = detail["champion"]["id"]
        # 该英雄自己的基线胜率 —— delta 要减它
        base = baselines.get(champion_id, {}).get("winRate")

        for r in detail.get("augments", {}).get("all", []):
            wr = r.get("winRate")
            awr = r.get("augmentWinRate")
            # 收益率 = 该英雄选这个符文的胜率 - 该英雄自己的基线胜率
            #
            # 注意不是减 augmentWinRate（那是符文的全局胜率）。
            # 一开始我减错了，结果：皮城执法官自身胜率只有 46.7%，
            # 他和任何符文的组合胜率都低于符文的全局平均，
            # 于是「三个候选收益全是负数」的警告天天误报。
            # 两个口径不只是数值不同，排序都可能反过来。
            delta = None if wr is None or base is None else wr - base

            rows.append((
                champion_id,
                r["id"],
                wr,
                awr,
                delta,
                r.get("sampleCount"),
                r.get("pickRate"),
                r.get("rank"),
                r.get("tier"),
                ",".join(str(s) for s in (r.get("availableStages") or [])),
            ))

    conn.executemany(
        "INSERT INTO champion_augment VALUES (?,?,?,?,?,?,?,?,?,?)", rows
    )
    return len(rows)


def insert_combos(conn, detail_dir: Path, baselines: dict) -> int:
    """把英雄详情里的 augmentCombinations 拍平成 champion_augment_combo。

    这是「两个符文一起选」的组合胜率，源数据每个英雄只给前 10 对。
    有了它才能回答「你上一轮选了 X，这一轮选哪个更搭」。
    """
    rows = []
    for path in sorted(detail_dir.glob("*.json")):
        detail = json.loads(path.read_text(encoding="utf-8"))
        champion_id = detail["champion"]["id"]
        base = baselines.get(champion_id, {}).get("winRate")

        for c in detail.get("augmentCombinations", []):
            ids = sorted(c.get("augmentIds") or [])
            if len(ids) != 2:
                continue
            wr = c.get("winRate")
            rows.append((
                champion_id,
                ids[0], ids[1],
                wr,
                None if wr is None or base is None else wr - base,
                c.get("sampleCount"),
                c.get("pickRate"),
                c.get("rank"),
            ))

    conn.executemany(
        "INSERT INTO champion_augment_combo VALUES (?,?,?,?,?,?,?,?)", rows)
    return len(rows)


# ==================== 主流程 ====================
def main():
    if DB.exists():
        DB.unlink()          # 每次全量重建，保证这个脚本可以反复运行

    meta = load("meta.json")
    conn = sqlite3.connect(DB)
    try:
        create_schema(conn)

        # 英雄自身的基线胜率来自排行榜 —— 算 delta 要用
        rankings = load("champion-rankings.json")
        baselines = {r["id"]: r for r in rankings["rows"]}

        n_champ = insert_champions(conn, load("champions.json"), baselines)
        n_aug = insert_augments(conn, load("augments.json"))
        n_ca = insert_details(conn, RAW / "champion-details", baselines)
        n_combo = insert_combos(conn, RAW / "champion-details", baselines)

        conn.executemany(
            "INSERT INTO meta VALUES (?,?)",
            [(k, v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
             for k, v in meta.items()],
        )

        conn.commit()

        print(f"champions         {n_champ:>7,} 行")
        print(f"augments          {n_aug:>7,} 行")
        print(f"champion_augment  {n_ca:>7,} 行")
        print(f"champion_augment_combo  {n_combo:>7,} 行")
        print(f"\n数据库: {DB.resolve()}  ({DB.stat().st_size / 1024:.0f} KB)")

        # ---- 自检 1：外键有没有悬空（有的话上面的 commit 早就报错了） ----
        orphans = conn.execute("""
            SELECT COUNT(*) FROM champion_augment ca
            LEFT JOIN champions c ON c.id = ca.champion_id
            LEFT JOIN augments  a ON a.id = ca.augment_id
            WHERE c.id IS NULL OR a.id IS NULL
        """).fetchone()[0]
        print(f"\n悬空外键: {orphans}")

        # ---- 自检 2：抽查一个英雄，用眼睛确认数据是对的 ----
        print("\n抽查：英雄 157 胜率最高的 5 个符文")
        cur = conn.execute("""
            SELECT a.name, ca.win_rate, ca.augment_win_rate, ca.delta, ca.sample_count
            FROM champion_augment ca
            JOIN augments a ON a.id = ca.augment_id
            WHERE ca.champion_id = 157
            ORDER BY ca.win_rate DESC
            LIMIT 5
        """)
        for name, wr, awr, delta, sc in cur.fetchall():
            print(f"  {name:<10} 胜率{wr:>7.2%}  全局{awr:>7.2%}  收益{delta:>+7.2%}  样本{sc:>9,}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
