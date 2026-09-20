"""步骤 4-c：识别三张卡片上的符文名称。

为什么还需要这一步？
    图标匹配只能把范围缩小到候选组 —— 实测 216 个符文里有 24 组共用图标
    （比如 1061 回力OK镖 和 2091 无尽大杀四方 用的是同一张图）。
    所以最后一步要用名称在候选组里挑出唯一那个。

    好在候选只有 2~9 个，OCR 不需要认对全部 216 个名字，
    只需要在已知的几个里挑最像的 —— 难度低得多。

名称区域的坐标是从 shot.png 上量出来的（分辨率锁死 1920x1080）。
如果裁出来的图不对，调整 NAME_TOP / NAME_BOTTOM / NAME_HALF_W 即可。

运行：
    python ocr_names.py
"""

from pathlib import Path

import cv2
import numpy as np
from rapidocr_onnxruntime import RapidOCR

SHOT = Path("shot.png")

# calibrate.py 标定出来的卡片位置
CARD_X = [439, 806, 1172]
CARD_Y = 192
CARD_W = [320, 321, 323]

# 名称文字在卡片内部的位置（相对卡片顶部）
NAME_TOP = 225          # 名称文字上沿
NAME_BOTTOM = 285       # 名称文字下沿
NAME_HALF_W = 140       # 名称区域左右各取多宽（以卡片中心为准）


def crop_name(img: np.ndarray, i: int) -> np.ndarray:
    """裁出第 i 张卡片的名称区域。i 从 0 开始。"""
    cx = CARD_X[i] + CARD_W[i] // 2
    y0 = CARD_Y + NAME_TOP
    y1 = CARD_Y + NAME_BOTTOM
    return img[y0:y1, cx - NAME_HALF_W: cx + NAME_HALF_W]


def main():
    img = cv2.imread(str(SHOT))
    if img is None:
        print(f"读不到 {SHOT}")
        return 1

    crops = []
    for i in range(3):
        crop = crop_name(img, i)
        crops.append(crop)
        cv2.imwrite(f"name_{i + 1}.png", crop)
        print(f"名称区域 #{i + 1}: {crop.shape[1]} x {crop.shape[0]}  -> name_{i + 1}.png")

    print("\n初始化 OCR（第一次要加载模型，几秒钟）...")
    engine = RapidOCR()

    print("\n识别结果:")
    for i, crop in enumerate(crops, 1):
        # RapidOCR 要 RGB，OpenCV 读进来是 BGR
        result, _ = engine(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
        if not result:
            print(f"  卡片 #{i}: 什么都没识别到")
            continue
        print(f"  卡片 #{i}:")
        for box, text, score in result:
            # 这个版本的 RapidOCR 把 score 返回成字符串，要自己转
            print(f"      「{text}」  置信度 {float(score):.3f}")

    print("\n对照答案: #1 牙仙子  #2 夜狩  #3 回力OK镖")
    print("打开 name_1~3.png 确认裁剪范围对不对。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
