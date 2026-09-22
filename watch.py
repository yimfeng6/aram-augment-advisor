"""盯着屏幕，等海克斯选择界面出现就自动识别并存图。

为什么需要这个？
    1. 攒测试样本 —— 手动 Alt+Tab 十几次太痛苦，而且打游戏时分心
    2. 这就是最终产品的雏形 —— 真正用的时候就是这个流程：
       发现界面 → 识别 → 给建议

怎么判断「现在是不是海克斯选择界面」？
    交给 recognize.detect_cards()：每帧现找画面里那三张亮边框卡片。
    一开始是写死坐标的，但我的游戏窗口从 1920x1080 变成 2000x1125 之后
    全废了 —— 所以改成每帧现找，分辨率随便变。

运行：
    python watch.py           一直盯着，Ctrl+C 退出
    python watch.py 600       盯 10 分钟后自动退出
"""

import ctypes
import sys
import time
from pathlib import Path

import cv2
import mss
import numpy as np
import win32gui

import advise
import liveapi
from recognize import Recognizer

SHOT_DIR = Path("shots")
PICKED_FILE = Path("picked.txt")     # 手动记录已选的符文 id，见 read_picked()
KEYWORD = "league"

POLL = 1.0          # 每隔多久看一次屏幕（秒）
ABSENT_RESET = 5    # 连续这么多帧看不到卡片，就认为离开了这个界面

SHOT_DIR.mkdir(exist_ok=True)

# mss 新版把 mss.mss 改名为 mss.MSS，但旧版没有。两种都兼容。
MSS_CLASS = getattr(mss, "MSS", None) or mss.mss


def enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()


def find_game_rect():
    """找到游戏窗口的客户区，返回 (x, y, w, h)。

    窗口最小化时 Windows 会把它挪到 (-32000, -32000) 且尺寸为 0，
    这时候返回 None —— 绝不能拿这个尺寸去截图，会直接抛异常。
    （这就是上次崩溃的原因。）
    """
    found = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title and KEYWORD in title.lower():
                l, t, r, b = win32gui.GetClientRect(hwnd)
                sx, sy = win32gui.ClientToScreen(hwnd, (l, t))
                found.append((sx, sy, r - l, b - t))
        return True

    win32gui.EnumWindows(cb, None)
    if not found:
        return None

    rect = max(found, key=lambda r: r[2] * r[3])
    x, y, w, h = rect
    if w <= 0 or h <= 0:
        return None
    return rect


def read_picked() -> list:
    """读 picked.txt —— 里面是人手动记下的「已经选过的符文 id」。

    为什么要靠人记，不能自动识别：
        游戏画面上**不显示**你已经选了哪些符文，Riot 的本地数据接口也不
        暴露这个信息（只有英雄、等级、装备）。查过了，确实没有。
        截图里卡片下方那排小图标是装备/锻造器，不是符文 —— 放大看过。

    所以这里留一个最简单的输入方式：每选一个符文，往 picked.txt 里加一个
    id（逗号或换行分隔都行）。文件不存在就当没选过。

    如果以后有人能识别出画面上的已选符文，只要改这一个函数。
    """
    if not PICKED_FILE.exists():
        return []
    try:
        text = PICKED_FILE.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for tok in text.replace(",", " ").replace("，", " ").split():
        if tok.strip().isdigit():
            out.append(int(tok.strip()))
    return out


def next_shot_path() -> Path:
    n = len(list(SHOT_DIR.glob("shot_*.png"))) + 1
    return SHOT_DIR / f"shot_{n:03d}.png"


def main():
    limit = float(sys.argv[1]) if len(sys.argv) > 1 else None
    started = time.time()

    enable_dpi_awareness()

    rect = find_game_rect()
    if rect is None:
        print(f"没找到标题含 '{KEYWORD}' 的窗口。先把游戏打开。")
        return 1

    print("初始化识别器（加载图标 + OCR 模型，几秒钟）...")
    recognizer = Recognizer()

    if rect[2] < 200 or rect[3] < 200:
        # 加载画面时窗口可能报成 1x1，看着像出错，其实不影响 ——
        # 循环里每帧都会重新查一次窗口大小，切到对局就正常了。
        print(f"提示：游戏窗口现在报的是 {rect[2]}x{rect[3]}（多半还在加载画面）。")
        print("      不影响使用，循环里每帧会重查窗口大小。")

    print(f"准备好了。游戏窗口 {rect[2]}x{rect[3]} @ ({rect[0]}, {rect[1]})")
    print("卡片位置每帧现找，所以改分辨率也没关系。")
    print(f"每隔 {POLL} 秒看一次屏幕，Ctrl+C 退出。\n")

    last_sig = None      # 上一次输出过的选项组合（三个 id 的元组）
    absent = 0           # 连续多少次没看到卡片
    refreshes = 0        # 当前这个界面里刷新过几次
    hits = 0
    misses = 0

    with MSS_CLASS() as sct:
        while True:
            if limit and time.time() - started > limit:
                print(f"\n到时间了，退出。共记录 {hits} 次。")
                break

            # 窗口可能被移动、缩放、最小化，每次重新查
            rect = find_game_rect()
            if rect is None:
                time.sleep(POLL)
                continue

            # 门禁：只有在对局进行中才去找海克斯界面。
            #
            # 英雄选择界面中间也有三张并排的卡片，几何上一模一样，
            # 光靠截图区分不开（踩过这个坑：把英雄卡认成了三个符文）。
            # 但那时候对局还没开始，本地接口连不上，所以用它做门禁最干脆。
            data = liveapi.fetch_all()
            if data is None:
                time.sleep(POLL)
                continue
            me = liveapi.get_me(data)

            sx, sy, w, h = rect
            raw = np.array(sct.grab({"left": sx, "top": sy, "width": w, "height": h}))
            img = raw[:, :, :3]          # BGRA -> BGR

            # recognize 内部会先找卡片、再检查内容；不是这个界面就返回空列表
            cards = recognizer.recognize(img)

            if not cards:
                misses += 1
                # 连续几秒看不到卡片，才认为真的离开了这个界面。
                # （不能一帧看不到就重置 —— 识别偶尔会漏一帧，那样会把
                #   同一个界面重复输出好几遍。）
                absent += 1
                if absent >= ABSENT_RESET:
                    last_sig = None
                    refreshes = 0
                time.sleep(POLL)
                continue

            absent = 0

            # 判断依据是「三个选项的组合变了没有」，不是时间。
            #
            # 一开始用的是时间冷却（20 秒内只触发一次），结果刷新符文之后
            # 新的三个选项被冷却挡在外面，直接不输出 —— 而刷新恰恰是最需要
            # 看一眼的时刻（刷新前那三个你可能已经不想选了）。
            # 按组合判断就自然多了：没变就不重复啰嗦，变了就立刻重新报。
            sig = tuple(c.augment_id for c in cards)
            if sig == last_sig:
                time.sleep(POLL)
                continue

            # 界面没离开但选项变了 = 刷新过一次
            if last_sig is not None:
                refreshes += 1

            last_sig = sig
            hits += 1

            path = next_shot_path()
            cv2.imwrite(str(path), img)

            tag = f"  【第 {refreshes} 次刷新后】" if refreshes else ""
            print(f"[{time.strftime('%H:%M:%S')}] 发现海克斯界面 -> {path.name}{tag}")

            # ---- 识别结果 ----
            for c in cards:
                mark = "✓" if c.augment_id else "?"
                dup = "  ⚠ 与其他卡片重复" if c.duplicate else ""
                print(f"    卡片 #{c.index} {mark}  {c.name:<16} ({c.method}){dup}")

            # ---- 推荐 ----
            if me is None:
                print("\n    （拿不到英雄信息，跳过推荐）\n")
                continue

            stage = advise.stage_from_level(me.get("level"))
            picked = read_picked()
            if picked:
                names = "、".join(advise.augment_name(a) for a in picked)
                print(f"    （已记录选了：{names}）")

            champion_name, suggestions = advise.advise(
                me["slug"], [c.augment_id for c in cards],
                stage=stage, picked=picked)

            print()
            if champion_name is None:
                print(f"    ⚠ {suggestions}")
            else:
                print(advise.format_advice(champion_name, suggestions, me.get("level")))
            print()

            time.sleep(POLL)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n\n手动退出。已保存的图都在 shots/ 里。")
