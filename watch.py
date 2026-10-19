"""盯着屏幕，等海克斯选择界面出现就自动识别、给建议、顺手存图。

**这个就是成品** —— 打游戏时开着它，出现海克斯选择界面就会打印该选哪个。

它还会把画面存进 shots/。这不是副作用，是顺带的攒样本：
手动 Alt+Tab 十几次太痛苦，于是让它自己存，回头能拿来跑 selftest。

「现在是不是海克斯选择界面」由四道关一起判断，全在 recognize.recognize()
里（空列表 = 不是这个界面）：

    1. 找卡片       每帧现找三张亮边框卡片，不依赖分辨率
    2. 内容检查     卡面必须是深色底（挡掉英雄选择界面的彩色原画）
    3. 对局门禁     本地接口连得上 = 在对局中（挡掉大厅、英雄选择）
    4. 关键词挡板   卡面出现「锻造器」→ 是属性锻造器界面，不是符文

    最后还有一道置信度门槛：三张里至少两张命中词表。

运行：
    python watch.py           一直盯着，Ctrl+C 退出
    python watch.py 600       盯 10 分钟后自动退出
"""

import sys
import time

import cv2
import numpy as np

import advise
import liveapi
from advise import read_picked
from overlay import Overlay
from recognize import Recognizer
from wincap import (MSS_CLASS, WINDOW_KEYWORD, enable_dpi_awareness,
                    find_game_rect, next_shot_path)


POLL = 1.0          # 每隔多久看一次屏幕（秒）
ABSENT_RESET = 5    # 连续这么多帧看不到卡片，就认为离开了这个界面
ABSENT_HIDE = 2     # 浮层藏得比状态重置早一点，免得选完符文还挂在屏幕上



def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    limit = float(args[0]) if args else None
    use_overlay = "--no-overlay" not in sys.argv
    started = time.time()

    enable_dpi_awareness()

    print("初始化识别器（加载图标 + OCR 模型，几秒钟）...")
    recognizer = Recognizer()

    ov = None
    if use_overlay:
        try:
            ov = Overlay()
            print("浮层已就绪。游戏要设成「无边框窗口（Borderless）」——")
            print("独占全屏下普通窗口盖不上去，这是所有非注入式浮层的共同限制。")
        except Exception as e:
            print(f"浮层启动失败（{type(e).__name__}: {e}），只输出到命令行。")

    # 双击启动时，人往往是先点图标再开游戏，所以这里等着，不直接退出
    rect = find_game_rect()
    if rect is None:
        print(f"没找到标题含 '{WINDOW_KEYWORD}' 的窗口。")
        print("等你打开游戏（Ctrl+C 退出）...")
        while rect is None:
            time.sleep(2)
            rect = find_game_rect()
        print("找到游戏窗口了。")

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
    last_advice = None   # 上一次画出来的 (cards, suggestions)，界面没变时复用

    with MSS_CLASS() as sct:
        while True:
            if limit and time.time() - started > limit:
                print(f"\n到时间了，退出。共记录 {hits} 次。")
                break

            # 窗口可能被移动、缩放、最小化，每次重新查
            rect = find_game_rect()
            if rect is None:
                if ov is not None:
                    ov.hide()
                time.sleep(POLL)
                continue

            # 门禁：只有在对局进行中才去找海克斯界面。
            #
            # 英雄选择界面中间也有三张并排的卡片，几何上一模一样，
            # 光靠截图区分不开（踩过这个坑：把英雄卡认成了三个符文）。
            # 但那时候对局还没开始，本地接口连不上，所以用它做门禁最干脆。
            data = liveapi.fetch_all()
            if data is None:
                if ov is not None:
                    ov.hide()
                time.sleep(POLL)
                continue
            me = liveapi.get_me(data)

            # 浮层如果被截进画面，OCR 可能读到自己画的字。
            # 能设上 WDA_EXCLUDEFROMCAPTURE 就当它不存在；
            # 设不上就在截图前藏一下 —— 一秒一次，基本看不出来。
            if ov is not None and not ov.capture_safe:
                ov.hide()

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
                if absent >= ABSENT_HIDE and ov is not None:
                    ov.hide()
                if absent >= ABSENT_RESET:
                    last_sig = None
                    refreshes = 0
                    last_advice = None
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
                # 界面没变。但刚才为了截图可能把浮层藏起来了，放回去。
                if ov is not None and last_advice is not None:
                    ov.show(rect, *last_advice)
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
                if ov is not None:
                    ov.hide()
                time.sleep(POLL)
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
                if ov is not None:
                    ov.hide()
            else:
                print(advise.format_advice(champion_name, suggestions, me.get("level")))
                if ov is not None:
                    ov.show(rect, cards, suggestions)
                    last_advice = (cards, suggestions)
            print()

            time.sleep(POLL)

    if ov is not None:
        ov.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n\n手动退出。已保存的图都在 shots/ 里。")
