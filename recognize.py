"""步骤 4：从游戏截图里认出三张卡片上的符文。

怎么认的（四步）：

    1. detect_cards()              在画面里找三张卡片。每帧现找，
                                   所以换分辨率、换窗口大小都不用改代码。
    2. card_looks_like_augment()   确认卡面是海克斯卡 —— 挡掉英雄选择界面
                                   那种「长得像但不是」的误判。
    3. OCR 整张卡片                取字号最大的那一行当名称（名称的字号
                                   明显大于类别标签和描述）。
    4. 在封闭词表里模糊匹配         词表只有 216 个合法名字，所以 OCR 读错
                                   一两个笔画（婪/欲 之类）也能吸附到对的。

    最后一道关：三张里至少两张认得出来，否则整帧丢弃。

关于「图标匹配」—— 设计过，量完之后停用了
    原本是「图标粗筛 + OCR 精筛」两级，指望两个信号互相验证。
    量完之后发现图标那一半根本不成立：

        216 个符文里有 24 组共用同一个图标
        （同一系列、升级版、「所选英雄技能」类各有一个通用图标），
        所以纯图标匹配原理上就不可能唯一识别。
        实测第一名正确率：19/93 = 20.4%。

    往一个 93/93 正确的系统里加一个 80% 错误的信号，只会把结果带偏。
    所以现在是纯 OCR + 封闭词表，图标那部分代码保留但默认关闭，
    详见 USE_ICON 的注释。

    这个反省写下来是因为：第一版文档里信誓旦旦写着「不要指望单一信号，
    每个信号都不可靠但合起来就可靠了」—— 那句话在**没量之前**听起来
    很有道理，量完之后是错的。

作为模块使用：
    from recognize import Recognizer
    r = Recognizer()
    cards = r.recognize(img)        # -> [Card, ...]
                                    # 空列表表示「这不是海克斯选择界面」

单独运行（做自检）：
    python recognize.py [图片或目录]
"""

import sqlite3
import sys
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import cv2
import numpy as np
from rapidocr_onnxruntime import RapidOCR

# ==================== 常量 ====================
SIZE = 128                       # 图标统一缩放到 128x128 再比较
ICON_DIR = Path("icons")
DB_PATH = Path("aramkit.db")

# 卡片内部的相对位置 —— 用「占卡片宽/高的比例」表示，而不是绝对像素。
#
# 这些比例是从 1920x1080 下量出来的绝对像素换算来的：
#     卡片 320x528 时，左右内缩 55px -> 55/320 = 0.172
# 用比例而不是像素，游戏分辨率一变就不用重做。
# （这是踩过坑之后改的：一开始写死了绝对坐标，我的游戏窗口从
#   1920x1080 变成 2000x1125 之后全部失效。）
PAD_X_FRAC = 0.172      # 图标区域左右内缩，避开卡片金边
PAD_TOP_FRAC = 0.085
ICON_BAND = 0.45        # 图标只出现在卡片上部这么高的范围内

# 这里原来还有三个常量：NAME_TOP_FRAC / NAME_BOTTOM_FRAC / NAME_HALF_W_FRAC，
# 用来按比例裁出「名称区域」。后来改成「整张卡片喂给 OCR，取字号最大的行」，
# 那套按比例裁剪的做法整个删掉了，常量却留了下来 —— 典型的改动没清干净。

GAME_THRESH = 70        # 游戏内图标分割阈值（含灰色部分）
ALPHA_THRESH = 10       # webp alpha 阈值

# 图标分数相差小于这个值，就认为「并列」，需要 OCR 来区分
TIE_MARGIN = 0.02

# 是否启用图标匹配 —— 默认关闭。
#
# 原本的设计是「图标粗筛 + OCR 精筛」，但量完之后图标这一半根本不成立：
#
#     图标第一名就对:  19/93 = 20.4%
#     即使只看分数 ≥0.8 的那批，也只有 71%
#
# 根本原因是 216 个符文里有 24 组共用同一个图标，纯图标匹配原理上就
# 不可能唯一识别。OCR 单独就已经 93/93，加进来只会往正确结果里掺噪声。
#
# 代码留着是为了将来 —— 如果哪天能用上更可靠的特征（比如卡片的稀有度边框
# 颜色），可以拿它当骨架。平时不要打开：每帧 648 次模板匹配纯属白烧 CPU。
USE_ICON = False


# ==================== 卡片检测（不依赖分辨率） ====================
def detect_cards(img: np.ndarray) -> list:
    """在画面里找海克斯卡片，返回 [(x, y, w, h), ...]（按 x 排序）。

    为什么不用写死的坐标：
        写死坐标 = 绑定分辨率。窗口大小一改就全废，
        而且失败时不会报错，只是识别结果莫名其妙变错。
        每帧现找虽然多花几毫秒，但换来了对分辨率的免疫力。

    做法：卡片边框是浅金色，在深色背景上非常亮。
          灰度 → 二值化 → 闭运算把边框断缝连起来 → 找轮廓。
    """
    h_img, w_img = img.shape[:2]

    # 窗口最小化/恢复的瞬间可能截到只有几像素的图
    if h_img < 100 or w_img < 100:
        return []

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # 只看画面中段：卡片不会贴顶也不会贴底。
    # 这条横带同时把画面下方的「C」按钮挡在检测范围外 ——
    # 放宽到 3%~97% 试过，结果闭运算会把卡片和按钮粘成一个 586 像素
    # 高的怪框，反而让「三个高度要相近」的检查否掉整帧。
    top, bottom = int(h_img * 0.08), int(h_img * 0.78)
    band = gray[top:bottom]

    _, mask = cv2.threshold(band, 110, 255, cv2.THRESH_BINARY)

    # 开运算：先腐蚀掉细的亮结构再膨胀回来。
    #
    # 用途是滤掉 JPEG 压缩的振铃噪点。高对比边界（比如涂黑的区域旁边）
    # 会产生一条条细亮线，闭运算把它们连成横贯画面的亮带，
    # 于是三张卡片被粘成一块，检测全灭。
    # 实测：不加这一步，JPEG 存过的测试样本 31 张只过 28 张；
    # 加上之后 31/31，原始截图也仍是 31/31。
    # 卡片边框约 5 像素宽，开运算的 3x3 核不会伤到它。
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    # 闭运算把边框上的断缝连起来。
    #
    # 试过改成「横长竖短」的核（25x3），想避免纵向粘连 —— 结果更糟：
    # 横向膨胀约 50 像素，而三张卡片只隔 75 像素，三张直接粘成一块，
    # 31 张截图一张都过不了。正方形核是验证过的配置，别乱动。
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                            np.ones((9, 9), np.uint8), iterations=2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        # 卡片大约占画面宽度的 1/6、高度的 1/2，用这个范围滤掉别的亮块。
        # 这个过滤只是粗筛，真正把关的是后面的内容检查
        # （card_looks_like_augment）。
        #
        # 注意：这几个比例是绑「完整截图」的。试过放宽它们去适配
        # 裁剪过的画面，结果连带把闭运算的核也改了，越改越糟。
        # 测试样本改成「涂黑敏感区域」而不是「裁剪」，几何就和线上一致了。
        if not (w_img * 0.10 < w < w_img * 0.25):
            continue
        if not (h_img * 0.30 < h < h_img * 0.70):
            continue
        boxes.append((x, y + top, w, h))

    boxes.sort()
    return boxes


def card_looks_like_augment(img: np.ndarray, box: tuple) -> bool:
    """从卡片内部的内容判断这是不是一张海克斯卡。

    为什么需要这一步（踩过的坑）：
        只靠「画面里有三个尺寸相近的矩形」是不够的 ——
        英雄选择界面中间那三张英雄卡完全符合这个几何特征，
        于是被当成了海克斯卡，识别出一堆莫名其妙的符文，
        而且全程不报错。这就是典型的「假阳性比报错更危险」。

    依据：
        海克斯卡片内部是近乎纯色的深青底 + 一个亮色图标；
        英雄卡内部是彩色原画，整体更亮、色调分布也不同。
        实测：海克斯卡内部亮度 ~30、饱和度 ~180；
              英雄卡内部亮度 48~117、饱和度 87~109。
    """
    x, y, w, h = box

    # 只取卡片上部中间那块，左右各内缩 15%、上下取 8%~45%。
    # 这样即使卡片框偏低（轮廓检测有时会多框一截，实测多出 77 像素），
    # 取样区依然稳稳落在卡片内部，不会取到外面的游戏场景。
    inner = img[y + int(h * 0.08): y + int(h * 0.45),
                x + int(w * 0.15): x + int(w * 0.85)]
    if inner.size == 0:
        return False

    gray = cv2.cvtColor(inner, cv2.COLOR_BGR2GRAY)
    sat = cv2.cvtColor(inner, cv2.COLOR_BGR2HSV)[:, :, 1].mean()

    return gray.mean() < 45 and sat > 150


def cards_are_valid(img: np.ndarray, boxes: list) -> bool:
    """判断找到的这几个框，是不是真的是「三张并排的海克斯卡片」。

    两道关：
        1. 几何 —— 三个尺寸相近、高度对齐、间距均匀的矩形
        2. 内容 —— 每张卡内部都得像海克斯卡（深色底，不是彩色原画）

    只有几何一道关是不够的，英雄选择界面会骗过它。
    """
    h_img, w_img = img.shape[:2]

    if len(boxes) != 3:
        return False

    ws = [b[2] for b in boxes]
    hs = [b[3] for b in boxes]
    ys = [b[1] for b in boxes]

    if max(ws) - min(ws) > w_img * 0.04:      # 三个宽度要相近
        return False
    if max(hs) - min(hs) > h_img * 0.06:      # 三个高度要相近
        return False
    if max(ys) - min(ys) > h_img * 0.04:      # 三个要对齐
        return False

    gaps = [boxes[i + 1][0] - boxes[i][0] for i in range(2)]
    if abs(gaps[0] - gaps[1]) > w_img * 0.03:  # 间距要均匀
        return False

    # 第二关：内容检查
    return all(card_looks_like_augment(img, b) for b in boxes)


@dataclass
class Card:
    """一张卡片的识别结果。"""
    index: int
    augment_id: int | None
    name: str
    method: str          # 靠什么确定的
    icon_score: float    # 图标匹配第一名的分数
    icon_best: int       # 图标匹配给出的答案
    icon_group: list     # 图标分数并列的候选（可能有多个）
    ocr_text: str        # OCR 原始读到的文字
    ocr_sim: float       # OCR 文字和最终答案的名字有多像
    ocr_best: int        # OCR 在词表里找到的最像的名字
    duplicate: bool = False   # 本帧内和其他卡片撞了 = 至少一个错了


# ==================== 图像工具 ====================
def imread(path):
    """读图片，能处理含中文的路径。

    OpenCV 在 Windows 上按 ANSI 码页解释路径，`cv2.imread` 遇到中文路径
    会返回 None，报 "can't open/read file"，看着像文件被删了。
    先读成字节再用 imdecode 就没这个问题。

    主程序用的是相对路径（纯 ASCII），不受影响；这个是给测试脚本用的，
    它们用的是含用户名的绝对路径。
    """
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def imwrite(path, img, params=None) -> bool:
    """写图片，同样能处理含中文的路径。"""
    ok, buf = cv2.imencode(Path(path).suffix, img, params or [])
    if ok:
        buf.tofile(str(path))
    return ok


def _is_skill_badge(text: str) -> bool:
    """判断这一行是不是技能角标（[Q] / [W] / [E] / [R]）。

    「你所选技能」类的符文会在卡片上多显示一个这样的角标，
    字号和符文名一模一样，不排掉的话会和名称抢位置。

    判据取得宽松一点：OCR 可能把方括号读成【】或圆括号，
    也可能漏掉半边，所以先把各种括号和空格剥掉再看剩下的。
    """
    core = text.strip().strip("[]【】()（）{} ").strip()
    return len(core) == 1 and core.upper() in "QWER"


def square_pad(img: np.ndarray) -> np.ndarray:
    """补成正方形，避免 resize 时被拉伸变形。"""
    h, w = img.shape[:2]
    side = max(h, w)
    canvas = np.zeros((side, side), np.uint8)
    canvas[(side - h) // 2:(side - h) // 2 + h,
           (side - w) // 2:(side - w) // 2 + w] = img
    return canvas


def tight_crop(gray: np.ndarray, mask: np.ndarray):
    """按掩码裁到紧贴外沿，掩码外涂黑。"""
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return None
    masked = cv2.bitwise_and(gray, gray, mask=mask)
    return masked[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


# ==================== 识别器 ====================
class Recognizer:
    """把图标模板、OCR 引擎、符文词表都装进来，一次初始化反复用。

    为什么要做成类？因为 OCR 引擎初始化要加载模型（好几秒）。
    如果每次识别都重新 new 一个，光初始化就耗掉大半时间。
    把这些「重」的资源挂在对象上，初始化一次，之后每次识别都是毫秒级。
    """

    def __init__(self):
        self.templates = self._load_templates()

        conn = sqlite3.connect(DB_PATH)
        self.names = dict(conn.execute("SELECT id, name FROM augments"))
        conn.close()

        self.ocr = RapidOCR()

    def _load_templates(self) -> dict:
        """读全部图标，走和游戏图完全一样的归一化流程。"""
        templates = {}
        for path in sorted(ICON_DIR.glob("*.webp")):
            img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
            if img is None:
                continue

            if img.ndim == 3 and img.shape[2] == 4:
                alpha = img[:, :, 3]
                mask = ((alpha > ALPHA_THRESH) * 255).astype(np.uint8)
                a = (alpha.astype(np.float32) / 255.0)[:, :, None]
                composited = (img[:, :, :3].astype(np.float32) * a).astype(np.uint8)
                gray = cv2.cvtColor(composited, cv2.COLOR_BGR2GRAY)
            else:
                mask = np.full(img.shape[:2], 255, np.uint8)
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            icon = tight_crop(gray, mask)
            if icon is None:
                continue
            templates[int(path.stem)] = cv2.resize(
                square_pad(icon), (SIZE, SIZE), interpolation=cv2.INTER_AREA)

        return templates

    # ---------- 第一级：图标 ----------
    def _extract_icon(self, img, box):
        x, y, w, h = box
        pad_x = int(w * PAD_X_FRAC)
        roi = img[y + int(h * PAD_TOP_FRAC): y + int(h * ICON_BAND),
                  x + pad_x: x + w - pad_x]

        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(gray, GAME_THRESH, 255, cv2.THRESH_BINARY)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        icon = tight_crop(gray, mask)
        if icon is None:
            return None
        return cv2.resize(square_pad(icon), (SIZE, SIZE),
                          interpolation=cv2.INTER_AREA)

    def _match_icon(self, icon) -> list:
        scores = []
        for aid, tpl in self.templates.items():
            res = cv2.matchTemplate(icon, tpl, cv2.TM_CCOEFF_NORMED)
            scores.append((aid, float(res[0, 0])))
        scores.sort(key=lambda t: -t[1])
        return scores

    # ---------- 第二级：名称 ----------
    def _ocr_card(self, img, box) -> list:
        """OCR 整张卡片的上半部分，返回原始结果 [[框, 文字, 置信度], ...]。

        做法：整张卡片直接喂给 OCR，让它自己找出所有文字行。

        为什么不做「按比例裁一个名称区」：
            那个做法依赖卡片框的精度，而卡片框是轮廓检测找出来的，
            有时会多框一截（实测多出 77 像素，卡片高 528 被框成 603）。
            框一歪，按比例算出的名称区就落到名字下方的类别标签上，
            OCR 只读到「伤害」「功能」二字，再被封闭词表吸附成
            一个乱七八糟的名字，而且完全不报错。
        """
        x, y, w, h = box
        # 只取上半部分 —— 下面的描述文字又小又密，送进去只会拖慢 OCR
        region = img[y: y + int(h * 0.62), x: x + w]
        if region.size == 0:
            return []
        result, _ = self.ocr(cv2.cvtColor(region, cv2.COLOR_BGR2RGB))
        return result or []

    @staticmethod
    def _pick_name(result: list) -> str:
        """从 OCR 结果里挑出名称 —— 字号最大、且位置靠下的那一行。

        卡片上除了名称，还有技能角标（[Q]/[E]）、类别标签、描述文字。

        为什么是「字号最大」：名称字号 ~20px，标签 ~15px，描述 ~11px，
        差得很明显，单靠字号就能把标签和描述滤掉。

        为什么还要「位置靠下」：
            踩过这个坑 —— 技能角标和名称的字号**完全相同**（都是 20.0），
            "你所选技能"类的符文会在卡片顶部多显示一个 [Q]/[E]。
            按字号排序时两者并列，Python 排序是稳定的，先出现的角标就赢了。
            症状：连续三次都是卡片 #1 认不出来，因为只有它会显示角标。
            名称永远在角标下方，所以并列时取靠下的那个。
        """
        if not result:
            return ""

        lines = []
        for bx, text, _score in result:
            if _is_skill_badge(text):
                continue          # 技能角标不是符文名
            top = min(p[1] for p in bx)
            bottom = max(p[1] for p in bx)
            lines.append((bottom - top, top, text))
        if not lines:
            return ""

        tallest = max(row[0] for row in lines)
        # 字号并列时取位置靠下的 —— 名称在角标下方。
        # 注意只在「完全并列」时才用这条规则：我一开始写成"差 10% 以内都算并列"，
        # 结果类别标签（18px）挤进了名称（20px）的候选，被选中成了「伤害」。
        tied = [row for row in lines if row[0] >= tallest - 0.01]
        tied.sort(key=lambda row: row[1])
        return tied[-1][2]

    # ---------- 合并两个信号 ----------
    @staticmethod
    def _sim(a: str, b: str) -> float:
        """两个字符串有多像，0~1。"""
        return SequenceMatcher(None, a, b).ratio()

    def recognize(self, img: np.ndarray, boxes: list | None = None) -> list:
        """识别三张卡片上的符文。

        返回空列表 = 「这看起来不是海克斯选择界面」。
        （不抛异常，因为调用方多半是在循环里盯着屏幕，没找到是正常情况。）
        """
        if boxes is None:
            boxes = detect_cards(img)
            if not cards_are_valid(img, boxes):
                return []

        cards = []

        for i, box in enumerate(boxes):
            # ---- 信号一：OCR 文字 → 在封闭词表里找最像的名字 ----
            ocr_lines = self._ocr_card(img, box)

            # 属性锻造器界面同样摆三张样式相同的卡片，但选项是属性碎片
            # （法术强度碎片、攻击力碎片、坚不可摧碎片……），不是符文。
            # 它的类别标签写着「属性锻造器」，OCR 读得到，用它挡掉。
            if any("锻造器" in text for _b, text, _s in ocr_lines):
                return []

            ocr_text = self._pick_name(ocr_lines)
            if ocr_text:
                text_ranked = sorted(self.names,
                                     key=lambda aid: -self._sim(ocr_text, self.names[aid]))
                text_best = text_ranked[0]
                text_sim = self._sim(ocr_text, self.names[text_best])
            else:
                text_ranked, text_best, text_sim = [], None, 0.0

            # ---- 信号二：图标匹配（默认关闭，见 USE_ICON 的说明）----
            if USE_ICON:
                icon = self._extract_icon(img, box)
                if icon is None:
                    icon_best, icon_score, group = None, 0.0, []
                else:
                    ranked = self._match_icon(icon)
                    icon_best, icon_score = ranked[0]
                    group = [aid for aid, s in ranked if icon_score - s <= TIE_MARGIN]
            else:
                icon_best, icon_score, group = None, 0.0, []

            # ---- 决策 ----
            #
            # 实测记录（6 张图 18 张卡片，全部人工核对过）：
            #
            #     判定分支               正确率
            #     OCR 精确命中词表        14/14
            #     「图标不可信」分支        0/4     ← 全错
            #
            # 那 4 次全错的原因是名称区裁歪了，OCR 只读到类别标签「伤害」，
            # 它和词表里所有名字的相似度都是 0.000，并列时按字典序
            # 随便挑了一个 —— 挑中了「泰坦的坚决」，而且看起来毫不心虚。
            # 裁歪的根因是卡片框不准（见 _ocr_card 的注释），修掉之后
            # OCR 读到的就是真名字，相似度自然很高。
            #
            # 所以现在是**纯 OCR 决策**，图标只作为报告信息，不参与判定。
            # 图标匹配在卡片框不准时表现很差（实测 top1 只有 0.27~0.41，
            # 正常应该是 0.65~0.85）。等卡片框稳定下来再考虑重新启用它。
            if text_best is None:
                # OCR 一行字都没读出来。以前这里是「退回图标匹配」，
                # 但图标准确率只有 20%，退回等于瞎猜，所以现在直接认输。
                chosen, method = None, "OCR 没读到文字"
            elif text_sim >= 0.99:
                chosen, method = text_best, "OCR 精确命中词表"
            elif text_sim >= 0.6:
                # 典型例子：「贪欲束缚」→「贪婪束缚」（婪/欲 形近，OCR 常读错）
                chosen, method = text_best, "OCR 模糊命中词表"
            else:
                # 读出来的东西和词表里任何名字都不像 —— 那就别猜了。
                #
                # 这里原来写的是"低置信度，可能不对"然后照样输出一个猜测。
                # 实测发现：属性锻造器界面上三张卡全被标了低置信度，
                # 但程序还是把三个错误答案连同推荐一起打印了出来。
                # 一个带着警告的错误答案，比直接说"不知道"更容易误导人。
                chosen, method = None, "认不出来（和词表里任何名字都不像）"

            cards.append(Card(
                index=i + 1,
                augment_id=chosen,
                name=self.names.get(chosen, "?") if chosen else "?",
                method=method,
                icon_score=icon_score,
                icon_best=icon_best,
                icon_group=group if len(group) > 1 else [],
                ocr_text=ocr_text,
                ocr_sim=text_sim,
                ocr_best=text_best,
            ))

        # 置信度门槛：三张里至少两张认得比较有把握，否则这多半根本不是
        # 海克斯选择界面。
        #
        # 实测踩过的坑：属性锻造器界面同样摆着三张样式相同的卡片，
        # 内容检查过得去，三张全都标了「低置信度」—— 但程序还是
        # 把三个猜测连同推荐一起打印了出来。我看到的是三个真实符文名
        # 加一份胜率表，完全不像出错的样子。
        #
        # 宁可什么都不输出，也不要输出一个看起来很像答案的错误答案。
        confident = sum(1 for c in cards
                        if c.augment_id is not None and c.ocr_sim >= 0.6)
        if confident < 2:
            return []

        # 一致性检查：一局里的三个选项永远不会重复。
        # 出现重复 = 至少有一张认错了，标出来让调用方知道。
        ids = [c.augment_id for c in cards]
        for c in cards:
            if c.augment_id is not None and ids.count(c.augment_id) > 1:
                c.duplicate = True

        return cards


# ==================== 自检 / 批量验证 ====================
def _print_detail(r: "Recognizer", c: Card):
    print(f"    符文      {c.name}  (id={c.augment_id})")
    print(f"    判定依据  {c.method}")
    print(f"    OCR 读到  「{c.ocr_text}」   相似度 {c.ocr_sim:.3f}"
          f"   -> {r.names.get(c.ocr_best, '?') if c.ocr_best else '?'}")
    # 图标那几行只在启用时才打印 —— 关掉的时候会打印一堆 "?"，看着像出错了
    if USE_ICON:
        print(f"    图标给出  {r.names.get(c.icon_best, '?') if c.icon_best else '?'}"
              f"   分数 {c.icon_score:.4f}")
        if c.icon_group:
            print(f"    图标并列  {[r.names.get(a) for a in c.icon_group]}")


def main():
    # 支持三种用法：
    #   python recognize.py                    看 shots/ 里的全部图片
    #   python recognize.py shot.png           看单张（输出详细信息）
    #   python recognize.py shots              看一个目录（输出汇总表）
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        Path("shots") if Path("shots").is_dir() else Path("shot.png"))

    files = sorted(target.glob("*.png")) if target.is_dir() else [target]

    if not files:
        print(f"{target} 下没找到图片")
        return 1

    print(f"共 {len(files)} 张图")
    print("初始化（加载图标模板 + OCR 模型）...")
    r = Recognizer()
    print(f"图标模板 {len(r.templates)} 个，符文词表 {len(r.names)} 个\n")

    detailed = len(files) == 1
    methods = {}

    for f in files:
        img = cv2.imread(str(f))
        if img is None:
            print(f"{f.name}: 读不到")
            continue

        cards = r.recognize(img)
        summary = " | ".join(f"{c.name}({c.augment_id})" for c in cards)
        print(f"{f.name:<16} {summary}")

        for c in cards:
            methods[c.method] = methods.get(c.method, 0) + 1
            if detailed:
                _print_detail(r, c)

    # 汇总：每种判定依据用了多少次 —— 看主力路径是不是符合预期
    print("\n判定依据统计:")
    for m, n in sorted(methods.items(), key=lambda t: -t[1]):
        print(f"    {m:<24} {n} 次")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
