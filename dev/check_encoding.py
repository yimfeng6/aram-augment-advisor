"""诊断：中文为什么乱码？

乱码有两种完全不同的原因，修法也完全不同：

  A. 文件本身坏了
     requests 用错了编码去解码服务器返回的字节，
     写进文件的就已经是垃圾了。

  B. 文件是好的，打开方式不对
     文件是正确的 UTF-8，但浏览器/编辑器按 GBK 去读它，
     看到的是「假乱码」。

这个脚本告诉你到底是哪一种。

运行：
    python check_encoding.py
"""

import re
import sys

import requests

# Windows 终端默认用 GBK 输出，直接 print 中文可能自己就乱了。
# 这一行强制用 UTF-8 输出，免得诊断结果本身被终端搞花。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

URL = "https://aramkit.com/zh-CN/augments"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# 页面上确定存在的一个词，用来验证解码对不对
PROBE = "珠光"


def main():
    resp = requests.get(URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    print("=== 1. 服务器怎么说的 ===")
    print("Content-Type :", resp.headers.get("Content-Type"))
    print("requests 采用 :", resp.encoding)
    print("requests 猜测 :", resp.apparent_encoding)

    print("\n=== 2. 页面自己有没有声明编码 ===")
    # 只取前 3000 字节，用 ascii 解码（忽略解不出来的），
    # 就足以找到 <meta charset=...> 这一行
    head = resp.content[:3000].decode("ascii", errors="ignore")
    match = re.search(r"""charset=["']?([\w-]+)""", head, re.I)
    print("声明了 :", match.group(1) if match else "（没有声明）")

    print("\n=== 3. 哪种编码能正确解码原始字节 ===")
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            text = resp.content.decode(enc)
            print(f"  {enc:8s} -> OK   probe_found={PROBE in text}")
        except UnicodeDecodeError as err:
            print(f"  {enc:8s} -> FAIL {err}")

    print("\n=== 4. 我们存的文件，开头 300 字符 ===")
    try:
        with open("debug_augments.html", encoding="utf-8") as f:
            print(f.read(300))
    except FileNotFoundError:
        print("（debug_augments.html 不存在，先跑 dump_page.py）")
    except UnicodeDecodeError as err:
        print("（文件不是合法 UTF-8，说明是原因 A：文件真的坏了）")
        print("  ", err)


if __name__ == "__main__":
    main()
