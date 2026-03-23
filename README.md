# Warframe 遗物锌价比查询工具

用于查询物品的锌价比（`ducats / 白金均价`），帮助筛选适合换金币的物品。

## 一分钟上手（推荐 GUI）

在 PowerShell 执行：

```powershell
Set-Location "C:\Users\pc\Desktop\随手文件\warframe小工具\垃圾查询"
D:\python\python.exe .\main.py
```

打开窗口后：

1. 在 `物品名 / 关键词` 输入物品（中文或英文都可）
2. `模式` 选 `exact`（精确）或 `contains`（关键词）
3. 点 `开始查询`
4. 下方 `结果列表` 查看高锌价比物品
5. 点 `导出 CSV` 保存结果

## 批量查询（GUI）

- 在 `批量查询（每行一个，可粘贴）` 区域直接粘贴多个查询词
- 或点击 `导入 txt/csv` 从文件导入
- 支持和上方单条查询一起使用，程序会自动去重
- 结果表会显示 `查询词` 列，便于你区分来源

## 从图片识别并查询（OCR）

先安装依赖：

```powershell
D:\python\python.exe -m pip install -r .\requirements.txt
```

然后执行：

```powershell
D:\python\python.exe .\main.py --ocr-image .\your_screenshot.png --threshold 10 --top 50 --sample-size 5 --debug
```

默认会使用 `auto` 模式（优先 `PaddleOCR`，不可用时自动回退 `RapidOCR`）。

如果你要手动指定：

```powershell
D:\python\python.exe .\main.py --ocr-image .\your_screenshot.png --ocr-engine paddle
D:\python\python.exe .\main.py --ocr-image .\your_screenshot.png --ocr-engine rapid
```

输出会给出：

- 识别到的可卖垃圾名称
- 对应数量（按图片识别聚合）
- ducats、均价、锌价比

可导出 CSV：

```powershell
D:\python\python.exe .\main.py --ocr-image .\your_screenshot.png --export-csv .\ocr_result.csv
```

也可以直接在 GUI 里做 OCR：

- 点击 `选择图片 OCR`，从本地选一张截图
- 或点击 `粘贴板图片 OCR`，直接读取剪贴板中的图片
- 在结果区也可以直接按 `Ctrl+V`，当剪贴板中有图片时会快速触发 OCR（无图片时不拦截普通粘贴）
- 识别后会自动走同样的查询和结果展示流程，并支持 `导出 CSV`

为了减少 OCR 误识别，程序会把识别到的名称先和本地 `items_catalog.json` 物品库比对：

- 能匹配到库内物品：继续查询价格与锌价比
- 匹配不到：直接跳过该名称，继续处理下一个

程序每次启动都会自动后台更新一次物品库；GUI 也提供 `更新物品库` 按钮可手动刷新。

物品库更新时会额外抓取 Huiji Wiki 常见物品中英对照（`curid=33551`）并合并进 `aliases`，
这样 OCR 识别到中文名称时会更容易映射到英文标准名（`item_name/url_name`）再去查价。

如果你有自定义补充词条（例如曲翼/曲翼枪械/曲翼近战），可编辑本地
`manual_aliases_zh.json`（格式：`分类 -> 中文名 -> 英文名`），更新物品库时会自动合并到
`items_catalog.json`。

## GUI 调试功能（看查询过程）

- 默认勾选 `显示调试日志`
- 查询时会在窗口底部 `调试日志` 显示：
  - 本次参数
  - 候选物品数量
  - 每个物品是否通过阈值
  - 最终结果数量

## 命令行用法（可选）

```powershell
D:\python\python.exe .\main.py "wisp prime 机体蓝图" --mode contains --threshold 10 --top 20 --sample-size 5 --debug
D:\python\python.exe .\main.py --queries "wisp prime 机体蓝图;khora prime chassis" --mode contains --export-csv .\batch.csv
D:\python\python.exe .\main.py --query-file .\queries.txt --mode contains --export-csv .\batch.csv
```

常用参数：

- `--debug`：打印详细过程日志
- `--export-csv .\high_ratio.csv`：导出结果
- `--gui`：强制启动图形界面
- `--queries`：批量查询词（逗号/分号分隔）
- `--query-file`：从 txt/csv 文件读取批量查询词
- `--ocr-image`：从截图 OCR 识别物品并直接查询
- `--ocr-engine`：OCR 引擎（`auto`/`paddle`/`rapid`）

## 说明

- 只统计 PC 平台、可见、在线/游戏内的 sell 订单。
- `sample-size` 表示取最便宜前 N 个卖单求均价。
- CSV 编码为 UTF-8 with BOM，便于 Windows Excel 打开。
