"""Regen 線上 lLISXTXC — 套用地圖功能正式上線給羅先生"""
import os, sys, re, json, base64, subprocess, urllib.request
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "api"))
from index import fetch_full_batch, gen_html, DEFAULT_CONTACT  # noqa

SHARE_ID = "lLISXTXC"
PAGE_URL = f"https://teddy-website-blog.pages.dev/share/{SHARE_ID}/"

print(f"[1] 抓線上 HTML")
req = urllib.request.Request(PAGE_URL, headers={"User-Agent": "Mozilla/5.0"})
html_old = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")

print("[2] 抓 client_name")
commits = json.loads(urllib.request.urlopen(
    f"https://api.github.com/repos/sky811117/teddy-shares/commits?path={SHARE_ID}/index.html&per_page=30",
    timeout=20
).read())
client_name = ""
for c in reversed(commits):
    nm = re.match(r"add:\s*(.+?)\s+\d+\s*戶\s*\(", c["commit"]["message"])
    if nm:
        client_name = nm.group(1).strip()
        break
print(f"    client_name = {client_name!r}")

slugs, seen = [], set()
for m in re.finditer(r"https://x\.ychouse\.tw/([A-Za-z0-9]+)", html_old):
    s = m.group(1)
    if s not in seen:
        seen.add(s); slugs.append(s)
print(f"[3] {len(slugs)} 個 slug")

properties = fetch_full_batch(slugs)
print(f"[4] fetch {len(properties)} 筆")
if len(properties) < len(slugs):
    print(f"    ⚠️ 有 {len(slugs)-len(properties)} 戶抓不到（下架？）")

fc = re.search(r'<div class="for-client">給\s*.+?\s*的專屬精選\s*·\s*(.+?)</div>', html_old, re.DOTALL)
need = fc.group(1).strip() if fc else "找房需求"

new_html = gen_html({
    "name": client_name, "need": need, "share_id": SHARE_ID, "contact": DEFAULT_CONTACT,
}, properties)

if "card-map" not in new_html or 'data-q="' not in new_html:
    print("❌ 地圖功能沒進去"); sys.exit(1)
print(f"[5] HTML 產出 {len(new_html)/1024:.1f}KB，含 {new_html.count('data-q=')} 個地圖")

print("[6] push GitHub")
api_path = f"repos/sky811117/teddy-shares/contents/{SHARE_ID}/index.html"
sha_json = subprocess.run(["gh", "api", api_path], capture_output=True, text=True, encoding="utf-8")
sha = json.loads(sha_json.stdout).get("sha") if sha_json.returncode == 0 else None

payload = {
    "message": f"regen: {SHARE_ID} 加入路段小地圖 ({len(properties)} 戶)",
    "content": base64.b64encode(new_html.encode("utf-8")).decode("ascii"),
    "branch": "main",
}
if sha: payload["sha"] = sha
result = subprocess.run(
    ["gh", "api", "-X", "PUT", api_path, "--input", "-"],
    input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8",
)
if result.returncode != 0:
    print(f"❌ push 失敗: {result.stderr[:300]}"); sys.exit(1)

resp = json.loads(result.stdout)
print(f"✅ commit: {resp.get('commit', {}).get('sha', '')[:10]}")
print(f"   {PAGE_URL}")
