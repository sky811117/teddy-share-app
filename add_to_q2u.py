"""把新 slug 補進 q2uGHkWF（現有 9 戶 → 10 戶）— 63tMRv 偶發 SSR timeout 加 retry"""
import os, sys, re, json, base64, subprocess, urllib.request, time
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "api"))
from index import fetch_full_batch, gen_html, DEFAULT_CONTACT, _fetch_one_full  # noqa

SID = "q2uGHkWF"
NEW_SLUGS = ["63tMRv", "6cnfBF", "895rQC", "97NcCH", "7qv39h"]

# 1. 拉現有頁面找 client name / need / 既有 slug
page_url = f"https://sky811117.github.io/teddy-shares/{SID}/"
html_old = urllib.request.urlopen(page_url, timeout=30).read().decode("utf-8")

commits_url = f"https://api.github.com/repos/sky811117/teddy-shares/commits?path={SID}/index.html&per_page=20"
commits = json.loads(urllib.request.urlopen(commits_url, timeout=20).read())
client_name = ""
for c in reversed(commits):
    msg = c["commit"]["message"]
    nm = re.match(r"add:\s*(.+?)\s+\d+\s*戶\s*\(", msg)
    if nm:
        client_name = nm.group(1).strip()
        break

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

to_add = [s for s in NEW_SLUGS if s not in slugs]
already_in = [s for s in NEW_SLUGS if s in slugs]
for s in already_in:
    print(f"⚠️ {s} 已在頁面內，跳過")
if not to_add:
    print("沒有要新增的 slug，結束")
    sys.exit(0)

print(f"client={client_name!r} need={need!r} existing={len(slugs)} + new {to_add}")

# 2. fetch 既有 slugs + 每個新 slug（SSR 偶發 timeout 個別 retry）
properties = fetch_full_batch(slugs)
print(f"既有 fetch 成功 {len(properties)} / {len(slugs)}")

for new_slug in to_add:
    new_prop = None
    for i in range(6):
        r = _fetch_one_full(new_slug)
        if not r.get("error") and r.get("community_display") and r.get("price"):
            new_prop = r
            break
        print(f"  {new_slug} retry {i+1}: {r.get('error') or 'no community/price'}")
        time.sleep(2)

    if not new_prop:
        print(f"❌ {new_slug} fetch 6 次都失敗，放棄此 slug")
        continue

    properties.append(new_prop)
    print(f"新增 {new_slug}: {new_prop['community_display']} {new_prop['price']}萬 {new_prop['layout']}")

print(f"總計 {len(properties)} 戶")

# 3. gen + push
new_html = gen_html({
    "name": client_name,
    "need": need,
    "share_id": SID,
    "contact": DEFAULT_CONTACT,
}, properties)

api_path = f"repos/sky811117/teddy-shares/contents/{SID}/index.html"
sha_json = subprocess.run(["gh", "api", api_path], capture_output=True, text=True, encoding="utf-8")
sha = json.loads(sha_json.stdout).get("sha") if sha_json.returncode == 0 else None

payload = {
    "message": f"add: {SID} 補 {','.join(to_add)} → {len(properties)} 戶",
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
