"""测试英雄联盟的本地数据接口（Live Client Data API）。

这是什么？
    游戏运行时，客户端会在本机开一个 HTTPS 端口 2999，
    对外提供当前对局的实时数据。游戏内的覆盖层就是用它。

    官方提供，本机访问，合规上没问题。

为什么我们需要它？
    步骤 5 要给出推荐，就必须知道「你在玩哪个英雄」。
    如果靠图像识别去认左下角的头像，要再匹配 173 个英雄图标，
    而且和符文一样可能遇到「多个英雄共用图标」的坑。
    这个接口直接给出英雄名和等级，一个像素都不用认。

怎么用：
    先进一局游戏（训练模式、人机、正常对局都行），
    然后在游戏进行中运行：
        python test_live_api.py

    如果返回一堆 JSON，说明能用。
    如果连不上，说明这个模式没开放这个接口，那我们就退回图像识别方案。
"""

import json
import sys
import urllib3

import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = "https://127.0.0.1:2999/liveclientdata"

# 这个接口用自签名证书，必须关掉证书校验，否则连不上
def get(path: str):
    return requests.get(f"{BASE}/{path}", verify=False, timeout=5)


def main():
    print("尝试连接本地数据接口 ...\n")

    try:
        r = get("allgamedata")
        r.raise_for_status()
    except Exception as exc:
        print(f"连不上: {type(exc).__name__}: {exc}\n")
        print("可能的原因：")
        print("  1. 现在没在游戏里（必须是在一局对局进行中）")
        print("  2. 这个模式没开放本地接口")
        print("  3. 端口被占用或防火墙拦了")
        return 1

    data = r.json()
    print("连上了。顶层字段：")
    for k, v in data.items():
        info = f"len={len(v)}" if hasattr(v, "__len__") and not isinstance(v, str) else ""
        print(f"  {k:<24} {type(v).__name__:<6} {info}")

    # ---- 我们最需要的：自己是谁 ----
    print("\n=== activePlayer（你自己）===")
    me = data.get("activePlayer", {})
    print(json.dumps(me, ensure_ascii=False, indent=2)[:800])

    # ---- 英雄名和等级藏在 allPlayers 里 ----
    print("\n=== allPlayers 里属于你的那条 ===")
    my_name = me.get("summonerName") or me.get("riotIdGameName")
    for p in data.get("allPlayers", []):
        if p.get("summonerName") == my_name or p.get("riotId") == my_name:
            slim = {
                "summonerName": p.get("summonerName"),
                "championName": p.get("championName"),
                "rawChampionName": p.get("rawChampionName"),
                "level": p.get("level"),
                "team": p.get("team"),
                "position": p.get("position"),
                "isBot": p.get("isBot"),
            }
            print(json.dumps(slim, ensure_ascii=False, indent=2))
            break
    else:
        print("没找到匹配的玩家，把上面 activePlayer 的输出贴给我")

    # ---- 看看有没有符文/强化相关的字段 ----
    print("\n=== 全部字段名里含 augment / perk / 强化的 ===")
    blob = json.dumps(data, ensure_ascii=False)
    for kw in ("augment", "Augment", "perk", "Perk", "hex", "Hex"):
        if kw in blob:
            print(f"  找到了 '{kw}'（可能存在相关字段）")
    else:
        print("  没找到")

    return 0


if __name__ == "__main__":
    sys.exit(main())
