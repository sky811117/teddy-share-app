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
| `GOOGLE_MAPS_EMBED_KEY` | Google Maps Embed API 金鑰（客戶頁的路段小地圖）。**沒設程式會直接報錯、整支啟動不了**。金鑰會嵌進客戶頁前端，靠 Google 端的限制保護：應用程式限制鎖 HTTP 參照網址（客戶頁實際掛的網域，清單見 `api/index.py` 這個常數上方的註解）、API 限制只開 Maps Embed API |

其他選填變數（Notion 瀏覽紀錄、TG 通知、覆蓋頁通關碼）列在 `.env.example`，只放變數名、不放值。

## 本地測試

Vercel function 本地不易直接跑（要 Vercel CLI），主要在生產環境測。

本機要跑任何會載入 `api/index.py` 的腳本（`regen_*.py`、`local_regen.py` 等）前，先在同一個 shell 設好 `GOOGLE_MAPS_EMBED_KEY`（程式不讀 `.env` 檔）：

```powershell
$env:GOOGLE_MAPS_EMBED_KEY = '<從 Google Cloud Console 憑證頁複製>'
```

離線單元測試（`python -B -m unittest discover -s tests_mp -v`）會自己塞一個假值，不需要真金鑰。
