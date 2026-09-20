"""步骤 1-a：把页面 HTML 存到本地，先看看它长什么样。

为什么第一步是这个？
    写爬虫的第一步永远是「看清楚数据长什么样」，而不是直接写解析代码。
    真实项目里没人会告诉你 HTML 结构，你得自己去看。
    这就是「跟教程抄」和「自己写」的第一个区别：
    教程会直接把选择器告诉你，而实际做项目时，你得先去看。

运行方式：
    python dump_page.py
"""

import requests

URL = "https://aramkit.com/zh-CN/augments"

# 很多网站会拒绝没有 User-Agent 的请求。
# requests 默认发的是 "python-requests/2.x"，一眼就被认出来是脚本。
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

OUTPUT = "debug_augments.html"


def main():
    # timeout 不能省：不加的话网络一卡，程序会永远挂在那里，你还以为它在跑
    resp = requests.get(URL, headers=HEADERS, timeout=15)

    # 4xx/5xx 直接抛异常，别让你对着空数据 debug 半小时
    resp.raise_for_status()

    # encoding="utf-8" 不能省：
    # Windows 下 open() 默认用 GBK 写文件，中文会变成乱码。
    # 这个坑在中文系统上极其常见。
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(resp.text)

    print(f"状态码: {resp.status_code}")
    print(f"页面大小: {len(resp.text):,} 字符")
    print(f"已保存到 {OUTPUT}")
    print("→ 用浏览器打开它，按 F12 看表格的标签结构")


if __name__ == "__main__":
    main()
