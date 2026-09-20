"""步骤 3：从游戏窗口截图。

目标：能在任意时刻把游戏窗口的画面存成一张 PNG。

这一步看着简单，但有两个真问题：

【问题 1】窗口 vs 全屏
    独占全屏模式下，普通的截图 API 会截到全黑画面，必须用 DXGI 桌面复制。
    你是窗口模式，所以没这个问题。

【问题 2】DPI 缩放 —— 2K 显示器上最容易踩的坑
    如果 Windows 的显示缩放不是 100%（2K 屏上很常见），
    Python 进程默认是「DPI 不感知」的：
        · 窗口 API 返回的坐标是「缩放后」的逻辑坐标
        · 截图 API 拿到的却是「缩放前」的物理像素
    两套坐标系对不上，结果就是截图位置整体偏移。
    解法是在程序最开始（任何窗口 API 调用之前）声明 DPI 感知。

运行：先打开游戏，进到「海克斯选择」界面，然后
    python capture.py
"""

import ctypes
import sys
import time
from pathlib import Path

import mss
import mss.tools
import win32gui

# 截图统一存到 shots/ 里，按序号命名，不会互相覆盖
SHOT_DIR = Path("shots")
KEYWORD = "league"          # 窗口标题里包含这个词（不区分大小写）


def next_shot_path() -> Path:
    """下一个可用的文件名：shots/shot_001.png、shot_002.png ..."""
    SHOT_DIR.mkdir(exist_ok=True)
    n = len(list(SHOT_DIR.glob("shot_*.png"))) + 1
    return SHOT_DIR / f"shot_{n:03d}.png"

# 运行后先等几秒再截图，给你时间切回游戏窗口。
# 不这么做的话，终端会挡住游戏，截出来的是终端自己。
DELAY = 6


def enable_dpi_awareness() -> None:
    """声明本进程是 DPI 感知的。

    必须在任何窗口 API 调用之前执行，否则不生效。
    没有这一句，在缩放不是 100% 的显示器上，截图会整体偏移。
    """
    try:
        # 2 = PROCESS_PER_MONITOR_DPI_AWARE，Win8.1+
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        # 老系统兜底
        ctypes.windll.user32.SetProcessDPIAware()


def list_windows(keyword: str) -> list[tuple[int, str]]:
    """列出所有可见窗口里，标题包含 keyword 的。"""
    found = []

    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title and keyword in title.lower():
                found.append((hwnd, title))
        return True

    win32gui.EnumWindows(callback, None)
    return found


def client_rect(hwnd: int) -> tuple[int, int, int, int]:
    """窗口「客户区」在屏幕上的位置和大小。

    客户区 = 不含标题栏和边框的那块区域，也就是游戏画面本身。
    用整窗口的 rect 会把标题栏也算进去，后面裁卡片时坐标就全错了。
    """
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    screen_x, screen_y = win32gui.ClientToScreen(hwnd, (left, top))
    return screen_x, screen_y, right - left, bottom - top


def main() -> int:
    enable_dpi_awareness()

    print(f"\n{DELAY} 秒后自动截图。现在马上切回游戏窗口（Alt+Tab），")
    print("让海克斯选择界面完整显示出来，别让终端挡住游戏。\n")
    for i in range(DELAY, 0, -1):
        print(f"  {i}...", end="", flush=True)
        time.sleep(1)
    print("  拍！\n")

    with mss.mss() as sct:
        print("显示器:")
        for i, mon in enumerate(sct.monitors):
            tag = "（虚拟桌面，所有显示器合并）" if i == 0 else ""
            print(f"  [{i}] {mon['width']}x{mon['height']} "
                  f"@ ({mon['left']}, {mon['top']}) {tag}")

        wins = list_windows(KEYWORD)
        if not wins:
            print(f"\n没找到标题含 '{KEYWORD}' 的窗口。")
            print("确认游戏已打开，且不是最小化状态。")
            return 1

        print(f"\n找到 {len(wins)} 个候选窗口:")
        for hwnd, title in wins:
            sx, sy, w, h = client_rect(hwnd)
            print(f"  [{hwnd}] {title}")
            print(f"          客户区 {w} x {h} @ ({sx}, {sy})")

        # 选客户区面积最大的那个 —— 游戏窗口总是最大的
        hwnd, title = max(wins, key=lambda t: client_rect(t[0])[2] * client_rect(t[0])[3])
        sx, sy, w, h = client_rect(hwnd)
        print(f"\n选中的窗口: {title}")

        shot = sct.grab({"left": sx, "top": sy, "width": w, "height": h})

    out = next_shot_path()
    mss.tools.to_png(shot.rgb, shot.size, output=str(out))
    print(f"\n已保存 {out.resolve()}")

    # ---------- 自检 ----------
    print(f"\n尺寸      {w} x {h}")
    print(f"宽高比    {w / h:.4f}   (16:9 = {16 / 9:.4f})")

    pixels = shot.rgb
    brightness = sum(pixels) / len(pixels)
    print(f"平均亮度  {brightness:.1f} / 255")
    if brightness < 5:
        print("  警告: 画面几乎全黑 —— 可能是独占全屏，或者窗口被遮挡了")

    print(f"\n用图片查看器打开 {out}，确认是游戏画面而不是黑屏/桌面。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
