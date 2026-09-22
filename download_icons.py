"""下载全部 216 个符文图标。

【可选步骤】—— 不跑这个也能正常用。

图标只被「图标匹配」用到，而那个功能实测第一名正确率只有 20.4%，
已经默认关闭（见 recognize.py 的 USE_ICON）。所以它现在不影响识别结果。

留着这个脚本是因为：
    · 万一以后能想出更可靠的图像特征，图标是现成的素材
    · 想看某个符文长什么样时，直接打开 icons/{id}.webp

图标 URL 规律：
    {endpoint}/assets/augment/{id}-{hash}.webp

注意：图标不挂在 resourcePath 下面，而是直接在 endpoint 根目录。
      这和词表、统计数据的路径规则不一样，容易搞混。

关于限速：这里的 SLEEP 比爬统计数据时小，因为这些是 CDN 上的静态小文件
（每个约 5KB），不是每请求一次都跑数据库的动态接口。即便如此也是 2 请求/秒，
够礼貌了，别再往下调。

支持断点续传：已存在的文件会跳过，中断了重跑就行。

运行：
    python download_icons.py
"""

import sqlite3
import time
from pathlib import Path

import requests

ENDPOINT = "https://data.aramkit.com"
DB = Path("aramkit.db")
OUT = Path("icons")

SLEEP = 0.5
TIMEOUT = 20
RETRIES = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def main():
    OUT.mkdir(exist_ok=True)

    conn = sqlite3.connect(DB)
    rows = conn.execute("SELECT id, name, icon FROM augments ORDER BY id").fetchall()
    conn.close()

    print(f"词表里 {len(rows)} 个符文")

    ok, skipped, failed = 0, 0, []

    for i, (aid, name, icon) in enumerate(rows, 1):
        dest = OUT / f"{aid}.webp"
        if dest.exists():
            skipped += 1
            continue

        for attempt in range(1, RETRIES + 1):
            try:
                r = SESSION.get(f"{ENDPOINT}/{icon}", timeout=TIMEOUT)
                r.raise_for_status()
                dest.write_bytes(r.content)
                ok += 1
                if i % 25 == 0 or i == len(rows):
                    print(f"  [{i}/{len(rows)}] 已下载 {ok} 个")
                time.sleep(SLEEP)
                break
            except Exception as exc:
                if attempt == RETRIES:
                    failed.append((aid, name, str(exc)))
                else:
                    time.sleep(attempt * 2)

    print(f"\n下载 {ok} 个，跳过 {skipped} 个，失败 {len(failed)} 个")
    for aid, name, err in failed[:10]:
        print(f"  ✗ {aid} {name}: {err}")

    # 自检：确认文件真的是图片，而不是错误页面
    bad = []
    for f in list(OUT.glob("*.webp"))[:5]:
        head = f.read_bytes()[:12]
        if not head.startswith(b"RIFF"):
            bad.append(f.name)
    print(f"\n抽检前 5 个文件头: {'全部是合法 WebP' if not bad else f'异常: {bad}'}")
    print(f"图标总数: {len(list(OUT.glob('*.webp')))}")


if __name__ == "__main__":
    main()
