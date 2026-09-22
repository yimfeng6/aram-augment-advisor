"""跑一遍测试样本，报告识别准确率。

为什么需要它
    README 里写着「测试样本全部正确」—— 这句话得有东西能证明，而且
    得能**随时重新证明**。改了识别逻辑之后跑一下，就知道有没有改坏。
    实测有用：有一次想把技能角标的问题"修"得更通用，结果把原本正确的
    「升级：耀光」改成了「伤害」，就是靠这个测试发现的。

样本哪来的
    tests/fixtures/ 里的图片是从真实游戏截图处理来的 —— **涂黑**了含召唤师
    ID 的区域，而不是裁掉。「涂黑」和「裁剪」的区别很关键（裁掉会改变画面
    几何，把检测参数带偏），生成脚本 tests/make_fixtures.py 里记了完整经过。

用法
    python selftest.py

退出码 0 = 全部通过，1 = 有失败（可以直接接进 CI）
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from recognize import Recognizer, imread  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
LABELS = ROOT / "tests" / "labels.json"


def main():
    if not LABELS.exists():
        print(f"找不到 {LABELS}")
        print("先跑 python tests/make_fixtures.py 生成测试样本")
        return 1

    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    positives = labels["positive"]
    negatives = labels["negative"]

    print("加载识别器 ...")
    recognizer = Recognizer()

    passed = failed = 0
    cards_ok = cards_total = 0

    print(f"\n=== 正样本（{len(positives)} 张）===")
    for name, expected in sorted(positives.items()):
        img = imread(FIXTURES / f"{name}.png")
        if img is None:
            print(f"  ✗ {name}  图片读不到")
            failed += 1
            continue

        got = [c.augment_id for c in recognizer.recognize(img)]
        cards_total += len(expected)
        cards_ok += sum(1 for a, b in zip(got, expected, strict=False) if a == b)

        if got == expected:
            passed += 1
            print(f"  ✓ {name}")
        else:
            failed += 1
            print(f"  ✗ {name}")
            print(f"      期望 {expected}")
            print(f"      实际 {got}")

    print(f"\n=== 负样本（{len(negatives)} 张，应该被拒绝）===")
    neg_passed = 0
    for name in negatives:
        img = imread(FIXTURES / f"{name}.png")
        if img is None:
            print(f"  ✗ {name}  图片读不到")
            continue
        cards = recognizer.recognize(img)
        if cards == []:
            neg_passed += 1
            print(f"  ✓ {name}  正确拒绝")
        else:
            print(f"  ✗ {name}  没被拒绝，识别成了 {[c.name for c in cards]}")

    # ---- 汇总 ----
    print("\n" + "=" * 50)
    print(f"正样本   {passed}/{passed + failed} 张整图全对")
    print(f"卡片     {cards_ok}/{cards_total} 张正确")
    print(f"负样本   {neg_passed}/{len(negatives)} 正确拒绝")

    ok = failed == 0 and neg_passed == len(negatives)
    print("\n全部通过 ✓" if ok else "\n有失败 ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
