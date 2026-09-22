"""Windows 上截取游戏窗口画面的公共部分。

capture.py 和 watch.py 都要做同样三件事：声明 DPI 感知、找到游戏窗口、
给截图起名字。这三件事原来在两个文件里各写了一份 —— 重复代码最要命的地方
不是多敲了几个字，而是**改一处、另一处还坏着**。

（这不是假设：calibrate.py 就是因为上游改了输出路径没同步，悄悄地坏掉了，
而它的报错信息还在指导用户去跑一个已经不会产生那个文件的脚本。
同一个毛病。）

所以抽到这里，两个脚本都从这里 import。
"""

import ctypes
from pathlib import Path

import mss
import win32gui

# mss 新版把 mss.mss 改名为 mss.MSS，旧版没有。两种都兼容。
MSS_CLASS = getattr(mss, "MSS", None) or mss.mss

WINDOW_KEYWORD = "league"      # 窗口标题里含这个词的才算（不区分大小写）
SHOT_DIR = Path("shots")


def enable_dpi_awareness() -> None:
    """声明本进程是 DPI 感知的。

    必须在任何窗口 API 调用之前执行，否则不生效。
    没有这一句，在缩放不是 100% 的显示器上，窗口坐标是「缩放后的逻辑值」，
    而截图 API 拿到的是「缩放前的物理像素」，两套坐标系对不上，
    截出来的位置会整体偏移 —— 而且不会报错，只是位置莫名不对。
    """
    try:
        # 2 = PROCESS_PER_MONITOR_DPI_AWARE，Win8.1+
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        # 老系统兜底
        ctypes.windll.user32.SetProcessDPIAware()


def find_game_rect(keyword: str = WINDOW_KEYWORD):
    """找到游戏窗口的客户区，返回 (x, y, w, h)；找不到或已最小化时返回 None。

    客户区 = 不含标题栏和边框的那块，也就是游戏画面本身。

    最小化时 Windows 会把窗口挪到 (-32000, -32000) 且尺寸为 0 ——
    这时候必须返回 None，绝不能拿这个尺寸去截图，会直接抛异常。
    """
    found = []

    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title and keyword in title.lower():
                left, top, right, bottom = win32gui.GetClientRect(hwnd)
                sx, sy = win32gui.ClientToScreen(hwnd, (left, top))
                found.append((sx, sy, right - left, bottom - top))
        return True

    win32gui.EnumWindows(callback, None)
    if not found:
        return None

    # 英雄联盟同时开着好几个窗口（客户端大厅、游戏本体）。
    # 与其硬编码窗口标题（标题会随版本和语言变），不如选客户区最大的那个 ——
    # 游戏窗口永远最大。不优雅，但活得久。
    rect = max(found, key=lambda r: r[2] * r[3])
    x, y, w, h = rect
    if w <= 0 or h <= 0:
        return None
    return rect


def next_shot_path() -> Path:
    """下一个可用的截图文件名：shots/shot_001.png、shot_002.png ..."""
    SHOT_DIR.mkdir(exist_ok=True)
    n = len(list(SHOT_DIR.glob("shot_*.png"))) + 1
    return SHOT_DIR / f"shot_{n:03d}.png"
