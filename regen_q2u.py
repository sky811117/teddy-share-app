"""Regen q2uGHkWF：原本格局 hardcode 3房2廳2衛 → 真實 layout 從 ycut 抓"""
import os, sys, re, json, base64, subprocess, urllib.request
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "api"))
from index import fetch_full_batch, gen_html, DEFAULT_CONTACT  # noqa

SID = "q2uGHkWF"
page_url = f"https://sky811117.github.io/teddy-shares/{SID}/"
html_old = urllib.request.urlopen(page_url, timeout=30).read().decode("utf-8")

# client name 從 first commit message 抓
commits_url = f"https://api.github.com/repos/sky811117/teddy-shares/commits?path={SID}/index.html&per_page=20"
commits = json.loads(urllib.request.urlopen(commits_url, timeout=20).read())
client_name = ""
for c in reversed(commits):
    msg = c["commit"]["message"]
    nm = re.match(r"add:\s*(.+?)\s+\d+\s*戶\s*\(", msg)
    if nm:
        client_name = nm.group(1).strip()
        break

# need 從現在 for-client 抓
fc = re.search(
    r'<div class="for-client">給\s*.+?\s*的專屬精選\s*·\s*(.+?)</div>',
    html_old, re.DOTALL,
)
need = "找房需求"
if fc:
    cand = fc.group(1).strip()
    if cand.startswith("找房需求") and "的" not in cand:
        need = cand

slugs, seen = [], set()
for m in re.finditer(r"https://x\.ychouse\.tw/([A-Za-z0-9]+)", html_old):
    s = m.group(1)
    if s not in seen:
        seen.add(s)
        slugs.append(s)
print(f"client={client_name!r} need={need!r} slugs={len(slugs)}")

properties = fetch_full_batch(slugs)
print(f"fetch 成功 {len(properties)}")
layouts = [(p["slug"], p.get("layout")) for p in properties]
for s, l in layouts:
    print(f"  {s}: {l!r}")

new_html = gen_html({
    "name": client_name,
    "need": need,
    "share_id": SID,
    "contact": DEFAULT_CONTACT,
}, properties)

# Sanity check：新 HTML 不應再有 hardcode 3房2廳2衛 全部一樣
hardcoded_count = new_html.count("3房2廳2衛")
print(f"new_html 中 3房2廳2衛 出現 {hardcoded_count} 次（>=1 OK 如果真的有 3房2廳2衛 物件）")

api_path = f"repos/sky811117/teddy-shares/contents/{SID}/index.html"
sha_json = subprocess.run(
    ["gh", "api", api_path], capture_output=True, text=True, encoding="utf-8"
)
sha = json.loads(sha_json.stdout).get("sha") if sha_json.returncode == 0 else None

payload = {
    "message": f"regen: {SID} 格局欄位 hardcode 改抓 ycut 真實值 ({len(properties)} 戶)",
    "content": base64.b64encode(new_html.encode("utf-8")).decode("ascii"),
    "branch": "main",
}
if sha:
    payload["sha"] = sha
result = subprocess.run(
    ["gh", "api", "-X", "PUT", api_path, "--input", "-"],
    input=json.dumps(payload),
    capture_output=True, text=True, encoding="utf-8",
)
if result.returncode != 0:
    print(f"❌ push 失敗: {result.stderr[:500]}")
    sys.exit(1)
print(f"✅ pushed: {page_url}")
