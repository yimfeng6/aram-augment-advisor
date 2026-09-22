"""从 shots/ 里的截图生成测试样本，供 selftest.py 使用。

为什么要处理，不能直接放原图
    整张截图里有召唤师 ID —— 自己的和同局其他玩家的都有：

        左侧记分板   x 0 ~ 9%                每个玩家的召唤师名
        左下聊天框   x 0 ~ 22.5%，y 53% ~ 68%  击杀播报里带名字
        顶部横幅     y 0 ~ 17%               "某某正在大杀特杀！"

    这些不能进公开仓库，得涂黑。

为什么是「涂黑」而不是「裁剪」—— 这里踩过坑
    第一版是把这些区域裁掉，只留中间一块。结果 31 张样本里有 6 张
    识别失败，而我一开始以为是识别出了问题。（另一版更糟：凭印象
    估聊天框的位置，结果一个字都没盖住，生成出来的样本里赫然躺着
    玩家自己的召唤师 ID —— 涂黑区域必须看图确认，不能估。）

    真正的原因是：**裁剪改变了画面几何**。detect_cards 里那些参数
    （尺寸占画面的比例、只看中间 8%~78% 的横带、闭运算的核大小）
    全是按完整截图的几何调出来的。裁掉两边之后：

        · 卡片占画面宽度 从 17% 涨到 22%，差点顶出尺寸上限
        · 卡片占画面高度 从 49% 涨到 69%，底部掉出横带
        · 卡片下方的「C」按钮进入检测范围，闭运算把它和卡片粘成一块

    我顺着这个方向改了三处参数，越改越糟 —— 最后一次把闭运算的核
    改成横长竖短，横向膨胀 50 像素而卡片只隔 75 像素，三张直接粘成
    一块，31 张**一张都过不了**。

    退回去换成涂黑，几何和线上完全一致，问题全部消失。

    教训：**测试样本要尽量保持和线上一致的输入形态**。为了"干净"
    去裁剪，等于顺手改了被测对象的输入分布。

用法
    python tests/make_fixtures.py

输出
    tests/fixtures/*.png      涂黑后的样本（无损，见下面的说明）
    tests/labels.json         每张样本的正确答案
"""

import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from recognize import Recognizer  # noqa: E402


# ---------------------------------------------------------------
# OpenCV 在 Windows 上用 ANSI 码页解释路径，读不了含中文的路径。
# 本脚本用的是绝对路径（含用户名），所以必须绕过。
# 主程序用的是相对路径，纯 ASCII，不受影响。
# ---------------------------------------------------------------
def imread_unicode(path):
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def imwrite_unicode(path, img, params=None):
    ok, buf = cv2.imencode(Path(path).suffix, img, params or [])
    if ok:
        buf.tofile(str(path))
    return ok


SHOTS = ROOT / "shots"
NEGATIVE_DIR = ROOT / "tests" / "negative"
OUT_DIR = ROOT / "tests" / "fixtures"
LABELS = ROOT / "tests" / "labels.json"

# 要涂黑的区域（占原图比例）：(左, 上, 右, 下)
#
# 这几个坐标是**看过图之后量出来的**，不是估的 —— 第一版凭印象写成
# 「聊天框在 y 76%~100%」，结果真正的位置是 y 53%~68%，一个字都没盖住，
# 生成出来的样本里赫然躺着玩家自己的召唤师 ID。
#
# 边界卡得很紧，因为左侧内容（x 最远到 22%）和第一张卡片（x 从 22.9% 开始）
# 只差不到 1%。改这几个数之前**必须重新看图**，不能凭感觉调。
REDACT = [
    (0.000, 0.000, 0.225, 1.000),   # 左侧：记分板 + 聊天框（聊天框到 x 22.5%）
    (0.000, 0.000, 1.000, 0.170),   # 顶部：击杀播报横幅 + FPS 浮层
    (0.000, 0.780, 1.000, 1.000),   # 底部 HUD
    (0.840, 0.000, 1.000, 1.000),   # 右侧记分板图标区（第三张卡片到 x 77.9%）
]

# 存 PNG，不存 JPEG —— 这一点是量出来的，不是偏好：
#
#     格式        31 张合计    识别正确
#     JPEG q82      3.4 M       30/31
#     JPEG q88      4.0 M       28/31
#     JPEG q90      4.3 M       28/31
#     JPEG q92      4.7 M       30/31
#     JPEG q95      5.9 M       28/31
#     JPEG q98      7.9 M       30/31
#     PNG          20.7 M       31/31   ← 只有它忠实
#
# JPEG 在任何质量下都到不了 31/31，而且不单调（q88 比 q82 还差）——
# 说明这是随机的噪声敏感性，不是"质量不够"。测试样本必须忠实反映
# 线上输入，而线上截图是屏幕原始像素、没有压缩。
#
# 也试过「PNG + 等比缩小」来压体积，结果更差：
#     1600x900 → 17/31     1280x720 → 23/31
# 重采样的模糊同样会伤到 OCR。
#
# 所以最终选择「原尺寸 PNG + 只保留一个覆盖关键场景的子集」。
# 体积从 20.7 MB 降到约 8 MB，而下面这 10 张覆盖了所有已知的坑。
IMAGE_EXT = ".png"
PNG_COMPRESSION = 9

# 保留哪些正样本。挑选依据是「覆盖到所有踩过的坑」，不是随便取的：
#
#   shot_001  基准情形
#   shot_004  卡片框被撑到 603 像素那批（框高 = 528 变 603 的 bug 现场）
#   shot_007  「升级：耀光」—— 技能角标和名称字号相同那个 bug 的现场
#   shot_011  循环往复 / 灵魂虹吸
#   shot_015  带 [Q] 角标
#   shot_020  三张卡片被闭运算粘成一块那批
#   shot_025  带 [Q] 角标 + 名称区域易受干扰
#   shot_028  质变：棱彩阶（棱彩稀有度，卡片配色和金/银不同）
#   shot_029  升级：死亡之舞（长名称）
#   shot_031  重量级打击手（长名称）
KEEP = {
    "shot_001", "shot_004", "shot_007", "shot_011", "shot_015",
    "shot_020", "shot_025", "shot_028", "shot_029", "shot_031",
}


def redact(img):
    h, w = img.shape[:2]
    out = img.copy()
    for l, t, r, b in REDACT:
        out[int(h * t):int(h * b), int(w * l):int(w * r)] = 0
    return out


def main():
    if not SHOTS.is_dir():
        print(f"找不到 {SHOTS}，先打几局用 watch.py 攒样本")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    recognizer = Recognizer()

    labels = {"positive": {}, "negative": []}

    print("=== 正样本 ===")
    for src in sorted(SHOTS.glob("*.png")):
        if src.stem not in KEEP:
            continue
        img = redact(imread_unicode(src))
        cards = recognizer.recognize(img)
        ids = [c.augment_id for c in cards]

        if not cards or None in ids:
            print(f"  ✗ {src.name}  识别不出来，跳过（原图本身就没认出来）")
            continue

        name = src.stem
        imwrite_unicode(OUT_DIR / f"{name}{IMAGE_EXT}", img,
                        [cv2.IMWRITE_PNG_COMPRESSION, PNG_COMPRESSION])
        labels["positive"][name] = ids
        print(f"  ✓ {name}  {ids}")

    print("\n=== 负样本（应该被拒绝）===")
    for src in sorted(NEGATIVE_DIR.glob("*.png")):
        img = redact(imread_unicode(src))
        name = f"negative_{src.stem}"
        imwrite_unicode(OUT_DIR / f"{name}{IMAGE_EXT}", img,
                        [cv2.IMWRITE_PNG_COMPRESSION, PNG_COMPRESSION])
        labels["negative"].append(name)

        rejected = recognizer.recognize(img) == []
        print(f"  {'✓' if rejected else '✗ 没被拒绝！'} {name}")

    LABELS.write_text(json.dumps(labels, ensure_ascii=False, indent=1),
                      encoding="utf-8")

    total = sum(len(v) for v in labels["positive"].values())
    size = sum(f.stat().st_size for f in OUT_DIR.iterdir() if f.is_file())
    print(f"\n生成 {len(labels['positive'])} 张正样本（{total} 张卡片）"
          f" + {len(labels['negative'])} 张负样本")
    print(f"合计 {size / 1024 / 1024:.1f} MB，答案写在 {LABELS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
