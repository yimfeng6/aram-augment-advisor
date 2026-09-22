"""步骤 3：从游戏窗口截图，存成一张 PNG。

目标：能在任意时刻把游戏窗口的画面存下来。

这一步看着简单，但有两个真问题：

【问题 1】窗口 vs 全屏
    独占全屏（exclusive fullscreen）下，普通截图 API 会截到全黑画面，
    得改用 DXGI 桌面复制。
    实测这款游戏是「全屏」模式，但走的是桌面合成，mss 能正常截到 ——
    所以暂时不需要特殊处理。真遇到全黑画面再换方案。

【问题 2】DPI 缩放
    如果 Windows 的显示缩放不是 100%，Python 进程默认是「DPI 不感知」的：
    窗口 API 返回「缩放后」的逻辑坐标，截图 API 拿到的却是物理像素，
    两套坐标系对不上，截出来的位置会整体偏移 —— 而且不报错。
    解法是在任何窗口 API 调用之前声明 DPI 感知。

窗口查找、DPI 声明、文件命名都在 wincap.py 里，和 watch.py 共用。

运行：先打开游戏，进到「海克斯选择」界面，然后
    python capture.py
"""

import sys
import time

import mss.tools

from wincap import (MSS_CLASS, WINDOW_KEYWORD, enable_dpi_awareness,
                    find_game_rect, next_shot_path)

DELAY = 6       # 运行后等几秒再截图，给你时间切回游戏窗口


def main() -> int:
    enable_dpi_awareness()

    print(f"\n{DELAY} 秒后自动截图。现在马上切回游戏窗口（Alt+Tab），")
    print("让画面完整显示出来，别让终端挡住游戏。\n")
    for i in range(DELAY, 0, -1):
        print(f"  {i}...", end="", flush=True)
        time.sleep(1)
    print("  拍！\n")

    with MSS_CLASS() as sct:
        print("显示器:")
        for i, mon in enumerate(sct.monitors):
            tag = "（虚拟桌面，所有显示器合并）" if i == 0 else ""
            print(f"  [{i}] {mon['width']}x{mon['height']} "
                  f"@ ({mon['left']}, {mon['top']}) {tag}")

        rect = find_game_rect(WINDOW_KEYWORD)
        if rect is None:
            print(f"\n没找到标题含 '{WINDOW_KEYWORD}' 的窗口。")
            print("确认游戏已打开，且不是最小化状态。")
            return 1

        sx, sy, w, h = rect
        print(f"\n游戏窗口客户区 {w} x {h} @ ({sx}, {sy})")
        shot = sct.grab({"left": sx, "top": sy, "width": w, "height": h})

    out = next_shot_path()
    mss.tools.to_png(shot.rgb, shot.size, output=str(out))
    print(f"\n已保存 {out.resolve()}")

    # ---------- 自检：当场判断这张图是不是能用 ----------
    print(f"\n尺寸      {w} x {h}")
    print(f"宽高比    {w / h:.4f}   (16:9 = {16 / 9:.4f})")

    # 黑屏是截图最典型的失败模式，而且文件会正常生成、大小正常，
    # 打开才发现是黑的。与其等人发现，不如程序自己先看一眼。
    pixels = shot.rgb
    brightness = sum(pixels) / len(pixels)
    print(f"平均亮度  {brightness:.1f} / 255")
    if brightness < 5:
        print("  警告: 画面几乎全黑 —— 可能是独占全屏，或者窗口被遮挡了")

    print(f"\n用图片查看器打开 {out}，确认是游戏画面而不是黑屏/桌面。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
