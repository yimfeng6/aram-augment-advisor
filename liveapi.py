"""读取英雄联盟的本地数据接口（Live Client Data API）。

游戏运行时，客户端会在本机 2999 端口开一个 HTTPS 服务，
提供当前对局的实时数据。这是官方提供的（游戏内覆盖层就用它），
只读、本机访问，合规上没问题。

我们需要它做三件事：

    1. 门禁 —— 只有在对局进行中才去找海克斯界面。
       英雄选择界面也有三张并排的卡片，几何特征一模一样，
       光靠截图区分不开（实测踩过这个坑）。但那时候对局还没开始，
       这个接口连不上，所以用它做门禁能从根上排除误判。

    2. 拿英雄 —— 推荐必须知道你在玩哪个英雄。
       用接口拿，比识别左下角头像靠谱得多。

    3. 拿等级 —— 海克斯在开局和 7/11/15 级出现，等级能反推出当前是第几阶段。
       （等级的「换算」在 advise.stage_from_level；这里只负责把原始值取出来。）

接口返回的字段（实测）：
    activePlayer.summonerName      "召唤师名#12345"
    allPlayers[].championName      "不祥之刃"
    allPlayers[].rawChampionName   "game_character_displayname_Katarina"
    allPlayers[].level             3
"""

import urllib3
import requests

# 本地接口用自签名证书，不关校验会连不上
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = "https://127.0.0.1:2999/liveclientdata"
TIMEOUT = 3


def fetch_all() -> dict | None:
    """拿当前对局的全部数据。

    不在对局中、游戏没开、或者接口被占用时返回 None —— 这是正常情况，
    调用方（比如 watch.py 的循环）不该为此中断。
    """
    try:
        r = requests.get(f"{BASE}/allgamedata", verify=False, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def get_me(data: dict) -> dict | None:
    """从全部数据里挑出「我自己」那一条，整理成我们需要的字段。"""
    me = data.get("activePlayer", {})
    my_name = me.get("summonerName") or me.get("riotIdGameName")
    if not my_name:
        return None

    for p in data.get("allPlayers", []):
        if p.get("summonerName") != my_name:
            continue

        # rawChampionName 形如 "game_character_displayname_Katarina"，
        # 取最后一段就是英文 ID，正好对上数据库里的 riot_id（不区分大小写）。
        raw = p.get("rawChampionName") or ""
        eng = raw.rsplit("_", 1)[-1] if raw else ""

        return {
            "summoner": my_name,
            "champion_cn": p.get("championName"),
            "champion_en": eng,
            "slug": eng.lower(),
            "level": p.get("level"),
            "team": p.get("team"),
        }
    return None


# 「等级 → 海克斯阶段」的换算在 advise.py 里（stage_from_level）。
# 这里原来也写了一份，是重复实现，已删 —— 判断依据是它和 advise 里那份
# 一个字都不差，而 watch.py 用的是 advise 那份。
