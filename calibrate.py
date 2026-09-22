"""调试工具：把识别流程找到的三张卡片画出来，看一眼框得准不准。

**主程序已经不用它了。**

它最早的作用是「标定卡片坐标」，好把坐标写死进代码。后来发现写死坐标
等于绑定分辨率（窗口从 1920x1080 变成 2000x1125 之后就全废了），
改成了 recognize.detect_cards() 每帧现找。所以「标定」这件事本身没必要了。

现在的用途只剩排查：
    · 识别出问题时，确认卡片到底框在哪
    · 换分辨率或窗口模式后，快速看一眼检测还准不准

跑完会生成 calib_boxes.png，打开看绿框有没有正好框住三张卡片。

运行：
    python calibrate.py                  用 shots/ 里最新的一张
    python calibrate.py shots/shot_003.png   指定一张
"""

import sys
from pathlib import Path

import cv2
import numpy as np

SHOT_DIR = Path("shots")
OUT_DIR = Path("debug")          # 调试产物统一丢这里，这个目录不进仓库
OUT_VIS = OUT_DIR / "calib_boxes.png"

# 卡片大概的尺寸范围，用来过滤掉图标、文字这些小块
MIN_W, MIN_H = 250, 400


def main():
    OUT_DIR.mkdir(exist_ok=True)
    # 用 shots/ 里最新的一张。也支持手动指定：python calibrate.py shots/shot_003.png
    if len(sys.argv) > 1:
        src = Path(sys.argv[1])
    else:
        shots = sorted(SHOT_DIR.glob("shot_*.png")) if SHOT_DIR.is_dir() else []
        if not shots:
            print(f"{SHOT_DIR}/ 里没有截图。先跑 watch.py 攒几张，或者 capture.py 截一张。")
            return 1
        src = shots[-1]
    print(f"用的样本: {src}")

    img = cv2.imread(str(src))
    if img is None:
        print(f"读不到 {src}")
        return 1

    h_img, w_img = img.shape[:2]
    print(f"图片尺寸: {w_img} x {h_img}")

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # 只取画面中间那条横带 —— 卡片只可能在这里，先把干扰排除掉
    top, bottom = int(h_img * 0.10), int(h_img * 0.75)
    band = gray[top:bottom]

    # 卡片边框很亮（接近白/金），背景很暗。
    # 阈值取 110 左右能把边框留下、背景滤掉。
    _, mask = cv2.threshold(band, 110, 255, cv2.THRESH_BINARY)

    # 闭运算：把边框上被图标/文字打断的缝隙连起来
    kernel = np.ones((9, 9), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w >= MIN_W and h >= MIN_H:
            boxes.append((x, y + top, w, h))     # y 要加回偏移

    boxes.sort()
    print(f"\n找到 {len(boxes)} 张卡片:")
    for i, (x, y, w, h) in enumerate(boxes, 1):
        print(f"  #{i}  x={x:>4}  y={y:>4}  w={w:>4}  h={h:>4}   "
              f"中心=({x + w // 2}, {y + h // 2})")

    if len(boxes) >= 2:
        gaps = [boxes[i + 1][0] - boxes[i][0] for i in range(len(boxes) - 1)]
        print(f"\n卡片间距: {gaps}  (应该三个值差不多，说明检测稳定)")

    # ---- 画出来人工确认 ----
    vis = img.copy()
    for i, (x, y, w, h) in enumerate(boxes, 1):
        cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 255, 0), 3)
        cv2.putText(vis, f"#{i} {w}x{h}", (x + 8, y + 36),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)

        # 顺带把「图标区」裁出来单独存 —— 先按卡片上部 35% 估
        icon_h = int(h * 0.35)
        crop = img[y:y + icon_h, x:x + w]
        cv2.imwrite(str(OUT_DIR / f"icon_region_{i}.png"), crop)

    cv2.imwrite(str(OUT_VIS), vis)
    print(f"\n已保存 {OUT_VIS} 和 icon_region_1~{len(boxes)}.png")
    print(f"打开 {OUT_VIS} 确认绿框是否正好框住三张卡片。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
