"""游戏内浮层：把胜率和「为什么选它」直接画在符文卡片下面。

为什么单独一个文件：
    watch.py 负责「认出屏幕上是什么」，这里只负责「画出来」。
    Tk 的窗口代码夹在识别循环里会很难读。

两个必须知道的限制：

1. **盖不住独占全屏。**
   这是一层普通窗口，不是注入游戏的 DirectX 浮层。
   游戏画面设置里要选「无边框窗口（Borderless）」，否则看不见。
   所有非注入式浮层（OBS、各种战绩助手）都有这个限制。

2. **它会被自己截图截进去。**
   watch.py 每隔一秒抓一次屏幕，浮层如果正好在画面上，OCR 就可能读到
   自己画出来的字。所以窗口创建后会尝试 SetWindowDisplayAffinity
   （WDA_EXCLUDEFROMCAPTURE，Win10 2004+），让截图 API 看不见它；
   内核太老设不上的话，watch.py 会在每次截图前先把它藏起来。
   能不能设上，看 Overlay.capture_safe。
"""

import ctypes
import tkinter as tk

import win32con
import win32gui

# 颜色键：这个颜色会被 Windows 抠成完全透明。
# 用 #010203 而不是纯黑 —— 纯黑可能撞上游戏画面本身的黑色。
KEY_COLOR = "#010203"

BG = "#0b111c"
BORDER = "#2b3a55"
FG = "#e7ecf5"
GOLD = "#e2c894"
GREEN = "#4ade80"
RED = "#f87777"
MUTED = "#93a1b8"

F_LABEL = ("Microsoft YaHei", 10, "bold")
F_RATE = ("Microsoft YaHei", 18, "bold")
F_BODY = ("Microsoft YaHei", 10)
F_SMALL = ("Microsoft YaHei", 9)

PAD = 10
HEADER_H = 34
LINE_H = 20

WDA_EXCLUDEFROMCAPTURE = 0x00000011
GWL_EXSTYLE = -20


class Overlay:
    """透明、置顶、鼠标穿透的一层画布。"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", KEY_COLOR)
        self.root.configure(bg=KEY_COLOR)

        self.canvas = tk.Canvas(self.root, bg=KEY_COLOR,
                                highlightthickness=0, borderwidth=0)
        self.canvas.pack(fill="both", expand=True)

        self.root.update_idletasks()
        self.hwnd = self._toplevel_hwnd()
        self._click_through()

        self.capture_safe = False      # 截图 API 是否已经看不见这层
        self._affinity_done = False
        self._rect = None

    # ==================== Win32 ====================
    def _toplevel_hwnd(self):
        hwnd = self.root.winfo_id()
        parent = win32gui.GetParent(hwnd)
        return parent or hwnd

    def _click_through(self):
        """让鼠标事件穿过去 —— 否则浮层会挡住点卡片。

        WS_EX_TRANSPARENT  点击直接落到下面的窗口
        WS_EX_TOOLWINDOW   不出现在任务栏和 Alt+Tab 里
        WS_EX_LAYERED      分层窗口，透明色键需要它
        """
        ex = win32gui.GetWindowLong(self.hwnd, GWL_EXSTYLE)
        win32gui.SetWindowLong(
            self.hwnd, GWL_EXSTYLE,
            ex | win32con.WS_EX_LAYERED
            | win32con.WS_EX_TRANSPARENT
            | win32con.WS_EX_TOOLWINDOW)

    def _exclude_from_capture(self):
        """让截屏看不见这层窗口。失败返回 False，调用方退回「截图前先藏」。"""
        try:
            ok = ctypes.windll.user32.SetWindowDisplayAffinity(
                self.hwnd, WDA_EXCLUDEFROMCAPTURE)
            return bool(ok)
        except Exception:
            return False

    # ==================== 显隐 ====================
    def show(self, rect, cards, suggestions):
        """rect 是游戏客户区 (x, y, w, h)；cards 是 recognize.Card 列表。"""
        x, y, w, h = rect
        if self._rect != rect:
            self.root.geometry(f"{w}x{h}+{x}+{y}")
            self.canvas.configure(width=w, height=h)
            self._rect = rect

        if not self.root.winfo_viewable():
            self.root.deiconify()
            self.root.update_idletasks()
            if not self._affinity_done:
                self.capture_safe = self._exclude_from_capture()
                self._affinity_done = True

        self._draw(cards, suggestions)
        self.root.update_idletasks()
        self.root.update()

    def hide(self):
        if self.root.winfo_viewable():
            self.root.withdraw()

    def close(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    # ==================== 画 ====================
    def _draw(self, cards, suggestions):
        self.canvas.delete("all")

        by_id = {s.augment_id: s for s in suggestions if s.augment_id}
        ranked = [s for s in suggestions if s.win_rate is not None]
        rank_of = {s.augment_id: i for i, s in enumerate(ranked)}

        deltas = [s.delta for s in ranked if s.delta is not None]
        all_below = bool(deltas) and all(d < 0 for d in deltas)

        for card in cards:
            box = getattr(card, "box", None)
            s = by_id.get(card.augment_id)
            if not box or s is None:
                continue
            self._draw_panel(box, s, rank_of.get(card.augment_id, 2), all_below)

    def _draw_panel(self, box, s, rank, all_below):
        bx, by, bw, bh = box
        panel_w = max(int(bw), 240)
        px = int(bx + (bw - panel_w) / 2)

        is_top = rank == 0
        label = {0: "首选", 1: "次选", 2: "备选"}.get(rank, "备选")
        reason = self._reason(s, is_top, all_below)
        combo = self._combo(s)

        # 理由太长时按字符数粗估一行放不放得下，放不下就给两行的高度
        chars_per_line = max(1, (panel_w - 2 * PAD) // 14)
        reason_lines = max(1, (len(reason) + chars_per_line - 1) // chars_per_line)

        panel_h = HEADER_H + reason_lines * LINE_H + (LINE_H if combo else 0) + 8

        canvas_h = self._rect[3] if self._rect else by + bh
        py = by + bh + 8
        if py + panel_h > canvas_h - 4:
            py = by - panel_h - 8          # 下方放不下就放卡片上方
        if py < 4:
            py = by + bh - panel_h - 6     # 上下都放不下就压在卡片下沿
        py = int(py)

        self.canvas.create_rectangle(
            px, py, px + panel_w, py + panel_h,
            fill=BG, outline=(GOLD if is_top else BORDER),
            width=(2 if is_top else 1))

        # 第一行：首选 / 次选 / 备选  +  胜率
        self.canvas.create_text(
            px + PAD, py + 8, anchor="nw", text=label,
            font=F_LABEL, fill=(GOLD if is_top else MUTED))

        rate_color = MUTED
        if s.delta is not None:
            rate_color = GREEN if s.delta > 0 else (RED if s.delta < 0 else FG)

        # 识别出的符文可能不在该英雄的数据里（正常不该发生），这时不该崩
        rate_text = f"胜率 {s.win_rate:.1%}" if s.win_rate is not None else "没有数据"

        self.canvas.create_text(
            px + panel_w - PAD, py + 4, anchor="ne",
            text=rate_text, font=F_RATE, fill=rate_color)

        # 第二行：为什么选它 / 为什么不选它
        ty = py + HEADER_H
        self.canvas.create_text(
            px + PAD, ty, anchor="nw", text=reason,
            font=F_BODY, fill=FG, width=panel_w - 2 * PAD)

        # 第三行：组合加成（有才画）
        if combo:
            self.canvas.create_text(
                px + PAD, ty + reason_lines * LINE_H, anchor="nw", text=combo,
                font=F_SMALL, fill=GREEN, width=panel_w - 2 * PAD)

    @staticmethod
    def _reason(s, is_top, all_below):
        if s.win_rate is None:
            return "没认出来，别选"
        d = s.delta
        if d is None:
            return "这个英雄没有该符文的数据"

        if is_top and all_below:
            text = "三个都低于平均，这个相对最好"
        elif is_top:
            text = f"三个里最高，比平均 {d:+.1%}"
        elif d >= 0:
            text = f"比平均 {d:+.1%}，备选"
        else:
            text = f"比平均 {d:+.1%}，不推荐"

        if s.sample_count is not None and s.sample_count < 1000:
            text += "（样本少）"
        return text

    @staticmethod
    def _combo(s):
        if not s.combo:
            return ""
        from advise import augment_name     # 局部 import，避免顶层循环依赖
        partner_id, win_rate, _delta, _n = s.combo
        return f"⚑ 和已选的「{augment_name(partner_id)}」组合胜率 {win_rate:.1%}"
