"""记录显示器和游戏窗口的变化，用来查"分辨率自己变了"到底是什么在变。

这是排查工具，不是项目的一部分。它只读不写，记录两张表：

    1. 显示器当前模式 —— 通过 EnumDisplaySettings 读系统设置
    2. 游戏窗口客户区 —— 和 watch.py 用的是同一套读法

任何一项发生变化就打印一行并写进日志文件，所以事后能对着时间点
看当时发生了什么。

用法：
    python monitor_display.py           一直盯着，Ctrl+C 退出
    python monitor_display.py 600       盯 10 分钟

日志写到 display_log.txt
"""

import ctypes
import sys
import time
from pathlib import Path

import mss
import win32api
import win32con
import win32gui

LOG = Path("display_log.txt")
KEYWORD = "league"
POLL = 2.0

MSS_CLASS = getattr(mss, "MSS", None) or mss.mss


def enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()


def display_mode():
    """系统当前的显示模式（物理像素）。"""
    try:
        dm = win32api.EnumDisplaySettings(None, win32con.ENUM_CURRENT_SETTINGS)
        return dm.PelsWidth, dm.PelsHeight, dm.DisplayFrequency
    except Exception as exc:
        return f"读取失败: {exc}", None, None


def monitor_list():
    """mss 看到的显示器尺寸 —— 和截图用的坐标系统一致。"""
    try:
        with MSS_CLASS() as sct:
            return [(m["width"], m["height"], m["left"], m["top"])
                    for m in sct.monitors]
    except Exception:
        return []


def game_windows():
    """所有标题含关键词的窗口及其客户区尺寸。"""
    found = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title and KEYWORD in title.lower():
                l, t, r, b = win32gui.GetClientRect(hwnd)
                sx, sy = win32gui.ClientToScreen(hwnd, (l, t))
                found.append((title, r - l, b - t, sx, sy))
        return True

    win32gui.EnumWindows(cb, None)
    return sorted(found)


def dpi_info():
    """系统缩放。缩放一变，桌面图标和文字会整体缩放，看着很像'分辨率变了'。"""
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
        return f"{dpi} ({dpi / 96 * 100:.0f}%)"
    except Exception as exc:
        return f"读取失败: {exc}"


def snapshot():
    w, h, hz = display_mode()
    return {
        "显示模式": f"{w}x{h} @ {hz}Hz",
        "系统缩放": dpi_info(),
        "显示器列表": str(monitor_list()),
        "游戏窗口": str(game_windows()),
    }


def main():
    limit = float(sys.argv[1]) if len(sys.argv) > 1 else None
    enable_dpi_awareness()

    logf = open(LOG, "a", encoding="utf-8")
    started = time.time()

    def emit(text):
        line = f"[{time.strftime('%H:%M:%S')}] {text}"
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    emit("=" * 60)
    emit("开始监视。只读，不会改动任何设置。")

    last = None
    while True:
        if limit and time.time() - started > limit:
            emit("到时间了，退出。")
            break

        now = snapshot()
        if now != last:
            if last is None:
                emit("初始状态：")
                for k, v in now.items():
                    emit(f"    {k}: {v}")
            else:
                emit("⚠ 检测到变化：")
                for k in now:
                    if now[k] != last[k]:
                        emit(f"    {k}:")
                        emit(f"        之前  {last[k]}")
                        emit(f"        现在  {now[k]}")
            last = now

        time.sleep(POLL)

    logf.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n退出。日志在 display_log.txt")
