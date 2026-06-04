"""本地 regen IwpaU8XV — 跳過 Vercel,本地產 HTML 後用 gh 推到 teddy-shares"""
import os, sys, re, json, base64, subprocess, urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "api"))
from index import fetch_full_batch, gen_html, DEFAULT_CONTACT

SHARE_ID = "IwpaU8XV"
PAGE_URL = f"https://sky811117.github.io/teddy-shares/{SHARE_ID}/"

print(f"[1/4] 抓既有頁面 slug 清單 — {PAGE_URL}")
html_old = urllib.request.urlopen(PAGE_URL, timeout=30).read().decode("utf-8")
slugs = []
seen = set()
for m in re.finditer(r"https://x\.ychouse\.tw/([A-Za-z0-9]+)", html_old):
    s = m.group(1)
    if s not in seen:
        seen.add(s)
        slugs.append(s)
print(f"     找到 {len(slugs)} 個 slug")

print(f"[2/4] fetch 物件資料(ycut)")
properties = fetch_full_batch(slugs)
print(f"     成功 fetch {len(properties)} 筆")

print(f"[3/4] 本地 gen_html")
client_data = {
    "name": "5/8日",
    "need": "找房需求",
    "share_id": SHARE_ID,
    "contact": DEFAULT_CONTACT,
}
new_html = gen_html(client_data, properties)
print(f"     HTML {len(new_html)} bytes")

# 寫本地檔案備份
local_path = os.path.join(os.path.dirname(__file__), f"new_{SHARE_ID}.html")
with open(local_path, "w", encoding="utf-8") as f:
    f.write(new_html)
print(f"     寫入 {local_path}")

# 驗證新 HTML 有沒有「權狀坪」
unit_count = new_html.count("萬 / 權狀坪")
old_count = new_html.count("萬 / 主建坪")
print(f"     驗證：權狀坪 {unit_count} 處 / 主建坪 {old_count} 處")
if old_count > 0 or unit_count == 0:
    print("     ❌ 本地 gen_html 有問題,中止")
    sys.exit(1)

print(f"[4/4] 用 gh 推到 sky811117/teddy-shares")
api_path = f"repos/sky811117/teddy-shares/contents/{SHARE_ID}/index.html"

# 先抓 sha
try:
    sha_json = subprocess.run(
        ["gh", "api", api_path], capture_output=True, text=True, encoding="utf-8", check=True
    )
    sha = json.loads(sha_json.stdout).get("sha")
    print(f"     舊檔 sha: {sha[:10]}...")
except Exception as e:
    print(f"     抓 sha 失敗: {e}")
    sha = None

# PUT 新內容
b64 = base64.b64encode(new_html.encode("utf-8")).decode("ascii")
payload = {
    "message": f"local regen: {SHARE_ID} 改用權狀坪公式 ({len(properties)} 戶)",
    "content": b64,
    "branch": "main",
}
if sha:
    payload["sha"] = sha

# gh api 用 stdin 傳 JSON
result = subprocess.run(
    ["gh", "api", "-X", "PUT", api_path, "--input", "-"],
    input=json.dumps(payload),
    capture_output=True, text=True, encoding="utf-8",
)
if result.returncode != 0:
    print(f"     ❌ push 失敗: {result.stderr[:500]}")
    sys.exit(1)

resp = json.loads(result.stdout)
commit_sha = resp.get("commit", {}).get("sha", "")[:10]
print(f"     ✅ commit: {commit_sha}")
print(f"     URL: {PAGE_URL}")
