# Teddy Share App

景泰客戶推薦頁產生器（Vercel 版）

## 架構

- `index.html` — 表單前端（手機友善，一格一格貼）
- `api/publish.py` — Vercel serverless function（後端）
- 後端流程：抓 ycut → 產 HTML → push 到 [sky811117/teddy-shares](https://github.com/sky811117/teddy-shares)
- 客戶頁 host 在 GitHub Pages：`https://sky811117.github.io/teddy-shares/{share_id}/`

## 環境變數

部署到 Vercel 時要設：

| Key | 說明 |
|---|---|
| `GITHUB_TOKEN` | Personal Access Token，需要 `sky811117/teddy-shares` 的 Contents 寫權限 |

## 本地測試

Vercel function 本地不易直接跑（要 Vercel CLI），主要在生產環境測。
