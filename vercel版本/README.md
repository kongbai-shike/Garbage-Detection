# Warframe 垃圾查询 - Vercel 部署版

这个目录是可直接部署到 Vercel 的 Web 版本，保留原项目的核心能力：

- 单条/批量查询（exact/contains）
- 目录匹配（`items_catalog.json` + 别名）
- OCR 图片识别查询（优先 `/api/ocr`；依赖缺失时自动回退浏览器 OCR + `/api/ocr-text`）
- CSV 下载（前端本地导出）

## 目录结构

- `api/index.py`: Flask Serverless API 入口
- `public/index.html`: 前端页面
- `analyzer.py`, `wfm_client.py`, `item_catalog.py`, `ocr_pipeline.py`: 核心逻辑
- `items_catalog.json`, `aliases_zh.json`, `manual_aliases_zh.json`: 数据文件
- `vercel.json`: Vercel 路由配置

## 本地启动（可选）

```powershell
cd "C:\Users\pc\Desktop\随手文件\warframe小工具\垃圾查询\vercel版本"
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python -m flask --app api/index.py run --port 8000
```

打开：`http://127.0.0.1:8000`

## 部署到 Vercel

1. 把 Vercel 项目的 Root Directory 设为 `vercel版本`
2. Framework Preset 选 `Other`
3. Build Command 留空（Vercel 自动识别 `api/index.py` + `vercel.json`）
4. Deploy

## API

- `GET /api/health`
- `POST /api/search`
- `POST /api/ocr` (form-data, key: `image`)
- `POST /api/ocr-text` (json, key: `text`)

示例 `POST /api/search` 请求体：

```json
{
  "queries": ["Lavos Prime 机体蓝图", "Khora Prime 系统蓝图"],
  "mode": "contains",
  "threshold": 10,
  "top": 20,
  "sample_size": 5
}
```

## 说明

- Vercel 文件系统是临时的，在线刷新目录写盘不是推荐方式。
- 建议在本地先更新 `items_catalog.json` 后再部署。
- 若 Vercel 未安装 OCR Python 依赖，前端会自动使用浏览器 OCR（首次识别会下载语言包，速度较慢）。

