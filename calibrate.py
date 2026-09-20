"""步骤 4-a：标定三张卡片的位置。

分辨率锁死在 1920x1080，所以卡片坐标本来是固定的常量。
但与其靠眼睛量像素，不如让程序自己找出来：
    · 更准 —— 眼睛量误差十几个像素很正常
    · 可验证 —— 把结果画出来看一眼就知道对不对
    · 换截图不用重来

做法：卡片边框是浅金色的，在深色背景上非常显眼。
      灰度 → 二值化 → 找轮廓 → 挑出面积大且是矩形的那些。

运行：
    python calibrate.py
"""

from pathlib import Path

import cv2
import numpy as np

SRC = Path("shot.png")
OUT_VIS = Path("calib_boxes.png")

# 卡片大概的尺寸范围，用来过滤掉图标、文字这些小块
MIN_W, MIN_H = 250, 400


def main():
    img = cv2.imread(str(SRC))
    if img is None:
        print(f"读不到 {SRC}，先跑 capture.py")
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
        cv2.imwrite(f"icon_region_{i}.png", crop)

    cv2.imwrite(str(OUT_VIS), vis)
    print(f"\n已保存 {OUT_VIS} 和 icon_region_1~{len(boxes)}.png")
    print("打开 calib_boxes.png 确认绿框是否正好框住三张卡片。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
