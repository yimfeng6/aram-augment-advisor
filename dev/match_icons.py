"""步骤 4-b：用图标匹配认出三张卡片上的符文。

思路：
    216 个符文的图标都是唯一的，而且游戏里用的就是同一套图标。
    所以「认出卡片上是哪个符文」= 「在 216 个图标里找最像的那一个」。
    不需要 OCR，对「回力OK镖」这种中英混排的名字天然免疫。

要解决的核心问题：两边的图长得不一样
    游戏里：图标在深青色卡片底上，带金色/灰色的渐变
    webp 里：图标在透明底上

    如果直接硬比，会被背景色和整体明暗带偏。
    解法是先把两边都归一化成「同一个东西」：

        1. 各自裁到紧贴图标外沿     —— 消除位置和留白差异
        2. 用掩码把背景涂成纯黑     —— 消除背景色差异
        3. 补成正方形再缩放到同尺寸 —— 消除长宽比和尺寸差异
        4. 用 TM_CCOEFF_NORMED 比较 —— 这个算法会自动减掉均值，
                                      所以对整体偏亮/偏暗也不敏感

    归一化做对了，匹配才可能对。这一步的功夫全在「把两边变成可比的东西」。

运行：
    python match_icons.py
"""

import sqlite3
from pathlib import Path

import cv2
import numpy as np

SIZE = 128                       # 统一缩放到 128x128 再比较
ICON_DIR = Path("icons")
SHOT = Path("shot.png")

# calibrate.py 标定出来的卡片位置。分辨率锁死 1920x1080，所以这是常量。
CARDS = [
    (439, 192, 320, 528),
    (806, 192, 321, 530),
    (1172, 190, 323, 529),
]

PAD_X = 55          # 左右内缩，避开卡片那圈亮金色边框
PAD_TOP = 45        # 顶部内缩
ICON_BAND = 0.45    # 图标只可能出现在卡片上部这么高的范围内

# 游戏内图标的分割阈值。
# 实测：50/60/70 都得到同一个外接框（说明稳定），
#       而 80 以上会把灰色的那部分滤掉，只剩亮色核心。
GAME_THRESH = 70

# webp alpha 通道的分割阈值 —— alpha 大于它才算图标的一部分
ALPHA_THRESH = 10


def square_pad(img: np.ndarray) -> np.ndarray:
    """补成正方形（黑边补齐）。

    不补的话，cv2.resize 会把图形按各自的长宽比拉伸，
    同一个图标在两边会变形得不一致，匹配分数直接掉下去。
    """
    h, w = img.shape[:2]
    side = max(h, w)
    canvas = np.zeros((side, side), np.uint8)
    canvas[(side - h) // 2:(side - h) // 2 + h,
           (side - w) // 2:(side - w) // 2 + w] = img
    return canvas


def tight_crop(gray: np.ndarray, mask: np.ndarray) -> np.ndarray | None:
    """按掩码裁到紧贴外沿，并把掩码外的像素涂成纯黑。"""
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return None

    y0, y1 = ys.min(), ys.max() + 1
    x0, x1 = xs.min(), xs.max() + 1

    # 掩码外涂黑 —— 这是消除「游戏深青底 vs webp 透明底」差异的关键
    masked = cv2.bitwise_and(gray, gray, mask=mask)
    return masked[y0:y1, x0:x1]


def load_templates() -> tuple[dict, dict]:
    """读全部图标，走和游戏图完全一样的归一化流程。"""
    conn = sqlite3.connect("aramkit.db")
    names = dict(conn.execute("SELECT id, name FROM augments"))
    conn.close()

    templates = {}
    for path in sorted(ICON_DIR.glob("*.webp")):
        aid = int(path.stem)
        img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)   # BGRA

        if img.ndim == 3 and img.shape[2] == 4:
            alpha = img[:, :, 3]
            mask = ((alpha > ALPHA_THRESH) * 255).astype(np.uint8)
            # 按 alpha 合成到黑底
            a = (alpha.astype(np.float32) / 255.0)[:, :, None]
            composited = (img[:, :, :3].astype(np.float32) * a).astype(np.uint8)
            gray = cv2.cvtColor(composited, cv2.COLOR_BGR2GRAY)
        else:
            mask = np.full(img.shape[:2], 255, np.uint8)
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        icon = tight_crop(gray, mask)
        if icon is None:
            continue
        templates[aid] = cv2.resize(square_pad(icon), (SIZE, SIZE),
                                    interpolation=cv2.INTER_AREA)

    return templates, names


def extract_game_icon(img: np.ndarray, card: tuple) -> np.ndarray | None:
    """从卡片里裁出图标，走和模板完全一样的归一化流程。"""
    x, y, w, h = card
    roi = img[y + PAD_TOP: y + int(h * ICON_BAND), x + PAD_X: x + w - PAD_X]

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, GAME_THRESH, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    icon = tight_crop(gray, mask)
    if icon is None:
        return None
    return cv2.resize(square_pad(icon), (SIZE, SIZE),
                      interpolation=cv2.INTER_AREA)


def match_one(icon: np.ndarray, templates: dict) -> list[tuple[int, float]]:
    """在全部模板里找最像的，返回按相似度降序的 (id, 分数)。"""
    scores = []
    for aid, tpl in templates.items():
        # 两张同尺寸的图做 TM_CCOEFF_NORMED，结果是 1x1 的相关系数，范围 -1~1
        res = cv2.matchTemplate(icon, tpl, cv2.TM_CCOEFF_NORMED)
        scores.append((aid, float(res[0, 0])))
    scores.sort(key=lambda t: -t[1])
    return scores


def main():
    img = cv2.imread(str(SHOT))
    if img is None:
        print(f"读不到 {SHOT}，先跑 capture.py")
        return 1

    templates, names = load_templates()
    print(f"加载图标模板 {len(templates)} 个\n")

    expected = {1: 2083, 2: 1326, 3: 1061}
    all_ok = True

    for i, card in enumerate(CARDS, 1):
        icon = extract_game_icon(img, card)
        if icon is None:
            print(f"卡片 #{i}: 没找到图标区域")
            all_ok = False
            continue

        cv2.imwrite(f"extracted_{i}.png", icon)
        ranked = match_one(icon, templates)

        hit = ranked[0][0] == expected[i]
        all_ok &= hit
        print(f"卡片 #{i}  {'✓ 正确' if hit else '✗ 错误'}   top5:")

        for aid, score in ranked[:5]:
            mark = "  ← 正确" if aid == expected[i] else ""
            print(f"    {score:>7.4f}   {names.get(aid, '?'):<14}(id={aid}){mark}")

        # 第一名和第二名的差距 —— 差距越大越可信
        if len(ranked) >= 2:
            print(f"    领先第二名 {ranked[0][1] - ranked[1][1]:.4f}")
        print()

    print("=" * 50)
    print("对照答案: #1 牙仙子(2083)  #2 夜狩(1326)  #3 回力OK镖(1061)")
    print("三张全对" if all_ok else "有错，需要继续调")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
