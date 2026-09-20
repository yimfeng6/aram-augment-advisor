# dev/

探索过程中的中间脚本，**不参与运行**。留在这里是因为它们记录了「怎么一步步搞清楚的」。

| 文件 | 当时在干什么 | 结论 |
|---|---|---|
| `dump_page.py` | 第一步：把 aramkit 的页面 HTML 存下来看结构 | 白忙一场——后来发现数据是静态 JSON，根本不用解析 HTML |
| `check_encoding.py` | 排查中文乱码 | 文件是好的，是浏览器打开本地 HTML 时缺 charset 声明 |
| `match_icons.py` | 用图标匹配认符文 | 被 `recognize.py` 取代。**顺带发现 216 个符文里有 24 组共用同一个图标**，纯图标匹配原理上不可能唯一识别 |
| `ocr_names.py` | 单独试 OCR 认名称 | 被 `recognize.py` 取代 |
| `test_live_api.py` | 验证 Riot 本地接口能不能用 | 能用，被 `liveapi.py` 取代 |
| `monitor_display.py` | 排查「分辨率自己变了」 | 结论：游戏被设成了全屏 1080p，进出游戏会让整个桌面切换分辨率。与本项目无关 |

## 运行前提

- `match_icons.py` 需要先跑 `download_icons.py`（依赖 `icons/`）
- `ocr_names.py` 需要 `shots/shot_001.png` 之类的样本
- `monitor_display.py` 是独立工具，直接跑
- 其余几个需要先跑过 `crawler.py`

这些脚本没有跟着主程序一起更新，跑不起来是正常的——它们只记录当时的思路。
