"""步骤 1：把 aramkit 的数据抓下来，存成本地 JSON。

数据源的 URL 规律：

    {endpoint}/data/versions.json                                    → 版本清单
    {endpoint}/{resourcePath}/{locale}/resources/augments.json       → 符文词表
    {endpoint}/{resourcePath}/{locale}/resources/champions.json      → 英雄词表
    {endpoint}/{dataPath}/stats/{dataset}/champion-rankings.json     → 英雄排行
    {endpoint}/{dataPath}/stats/{dataset}/champion-details/{id}.json → 英雄×符文交叉数据

关键：dataPath 和 resourcePath 每次版本更新都会变（路径里带哈希），
     所以必须先读 versions.json 拿到它们，绝对不能硬编码。

为什么只下载、不建库？
     下载慢、依赖网络、失败要重跑，所以单独成一步，并且支持断点续传。
     建库快、纯本地，可以反复重跑来调数据结构。
     原始数据留一份，建库逻辑写错时不用重新下载。
     这叫「分层」——是工程能力里最基本的一条。

运行：
    python crawler.py
"""

import json
import sys
import time
from pathlib import Path

import requests

# ==================== 配置 ====================
ENDPOINT = "https://data.aramkit.com"
LOCALE = "zh-CN"
DATASET = "all"          # all = 全段位；想换成高分段就改成 "high"
OUT_DIR = Path("raw")

SLEEP = 1.5              # 每次请求之间的间隔（秒）。这是对别人服务器的礼貌，别调小
TIMEOUT = 30             # 单次请求超时（秒）
RETRIES = 3              # 失败重试次数

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# Session 会复用 TCP 连接，比每次新建快很多
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ==================== 基础设施 ====================
def get_json(url: str) -> dict:
    """下载一个 JSON。失败自动重试，成功后自动限速。"""
    for attempt in range(1, RETRIES + 1):
        try:
            resp = SESSION.get(url, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            time.sleep(SLEEP)        # 限速放在这里，保证每一次请求都被限制
            return data
        except Exception as exc:
            print(f"      [第 {attempt}/{RETRIES} 次失败] {type(exc).__name__}: {exc}")
            if attempt == RETRIES:
                raise
            time.sleep(attempt * 3)  # 退避重试：越失败等得越久

    # 循环体能保证要么 return 要么 raise，这里理论上到不了。
    # 留着是为了防「把 RETRIES 改成 0」这种改动 —— 那样循环一次都不执行，
    # 函数会静默返回 None，调用方拿到 None 还会继续往下跑。
    raise RuntimeError(f"重试次数配置有误：RETRIES={RETRIES}")


def save_json(path: Path, data) -> None:
    """写 JSON 到文件。encoding 必须显式指定，否则中文会乱码。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


# ==================== 主流程 ====================
def main():
    OUT_DIR.mkdir(exist_ok=True)
    started = time.time()

    # ---------- 1. 版本清单 ----------
    print("[1/4] 读取版本清单 ...")
    versions = get_json(f"{ENDPOINT}/data/versions.json")

    latest = versions["latest"]
    row = next(v for v in versions["versions"] if v["version"] == latest)
    data_path = row["dataPath"]
    resource_path = row["resourcePath"]

    print(f"      版本 {latest}   数据日期 {row.get('dataDate')}")
    print(f"      总对局数 {row.get('allMatches', 0):,}")

    # 记下这次抓的是什么版本 —— 将来数据对不上时分得清是哪一批
    save_json(OUT_DIR / "meta.json", {
        "endpoint": ENDPOINT,
        "locale": LOCALE,
        "dataset": DATASET,
        "version": latest,
        "dataPath": data_path,
        "resourcePath": resource_path,
        "crawledAt": time.strftime("%Y-%m-%d %H:%M:%S"),
    })

    # ---------- 2. 词表 ----------
    print("[2/4] 下载词表 ...")
    base_res = f"{ENDPOINT}/{resource_path}/{LOCALE}/resources"

    augments = get_json(f"{base_res}/augments.json")
    save_json(OUT_DIR / "augments.json", augments)
    print(f"      符文 {len(augments)} 个")

    champions = get_json(f"{base_res}/champions.json")
    save_json(OUT_DIR / "champions.json", champions)
    print(f"      英雄 {len(champions)} 个")

    # ---------- 3. 英雄排行（顺便拿到全部英雄 id） ----------
    print("[3/4] 下载英雄排行 ...")
    base_data = f"{ENDPOINT}/{data_path}/stats/{DATASET}"

    rankings = get_json(f"{base_data}/champion-rankings.json")
    save_json(OUT_DIR / "champion-rankings.json", rankings)

    ids = [r["id"] for r in rankings["rows"]]
    print(f"      共 {len(ids)} 个英雄待抓")

    # ---------- 4. 逐个英雄的详情 ----------
    print(f"[4/4] 下载英雄详情（预计 {len(ids) * SLEEP / 60:.1f} 分钟）...")
    detail_dir = OUT_DIR / "champion-details"
    skipped = 0

    for i, cid in enumerate(ids, 1):
        out = detail_dir / f"{cid}.json"

        # 断点续传：已下载的跳过。中途断了重跑不用从头来
        if out.exists():
            skipped += 1
            continue

        detail = get_json(f"{base_data}/champion-details/{cid}.json")
        save_json(out, detail)
        print(f"      [{i}/{len(ids)}] {cid} OK")

    print(f"      完成，跳过已存在的 {skipped} 个")
    print(f"\n全部完成，用时 {(time.time() - started) / 60:.1f} 分钟")
    print(f"数据在 {OUT_DIR.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n手动中断。已下载的文件都保留着，重新运行会从断点继续。")
        sys.exit(1)
