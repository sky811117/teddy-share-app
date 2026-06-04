# -*- coding: utf-8 -*-
"""從 lvr_buildings.db 萃取台中各區實價登錄統計 -> taichung_lvr_stats.json
僅報歷史成交（有來源、不預測未來），中位數 + p25/p75 區間，排除車位/店面/廠辦/極端值。
"""
import sqlite3, json, statistics, os
from collections import defaultdict

DB = r"C:\Users\a0920\房仲工作站\200_泰迪房地開發\data\lvr_buildings.db"
OUT = r"C:\Users\a0920\teddy-share-app\_data\taichung_lvr_stats.json"

M2_PER_PING = 3.305785
RESIDENTIAL = ("住宅大樓(11層含以上有電梯)", "華廈(10層含以下有電梯)",
               "公寓(5樓含以下無電梯)", "透天厝", "套房(1房1廳1衛)")
TYPE_SHORT = {
    "住宅大樓(11層含以上有電梯)": "電梯大樓",
    "華廈(10層含以下有電梯)": "華廈",
    "公寓(5樓含以下無電梯)": "公寓",
    "透天厝": "透天厝",
    "套房(1房1廳1衛)": "套房",
}
# 近 2 年窗 / 前 1 年窗（民國 7 碼 deal_date 字串比較）
RECENT_FROM = "1130601"   # 約 2024-06 之後（近 2 年）
RECENT_1Y   = "1140601"   # 近 1 年
PRIOR_1Y_LO = "1130601"   # 前 1 年下界
PRIOR_1Y_HI = "1140601"   # 前 1 年上界

def ping_price(unit_price_m2):
    return unit_price_m2 * M2_PER_PING / 10000.0  # 萬/坪

def pct(vals, p):
    if not vals: return None
    s = sorted(vals); k = (len(s) - 1) * p / 100.0
    f = int(k)
    if f + 1 < len(s): return s[f] + (s[f+1]-s[f]) * (k - f)
    return s[f]

c = sqlite3.connect(DB); cur = c.cursor()
rows = cur.execute("""
  SELECT district, unit_price_m2, age_years, building_type, rooms, deal_date, total_price
  FROM lvr_buildings
  WHERE district LIKE '%區' AND deal_date >= ?
    AND unit_price_m2 BETWEEN 30000 AND 450000
    AND building_area_m2 > 0
    AND building_type IN (?,?,?,?,?)
""", (RECENT_FROM, *RESIDENTIAL)).fetchall()
c.close()

by_dist = defaultdict(list)
for d in rows: by_dist[d[0]].append(d)

districts = []
for dist, recs in by_dist.items():
    prices = [ping_price(r[1]) for r in recs]
    if len(prices) < 30:   # 樣本太少不單獨成列（避免誤導）
        continue
    ages = [r[2] for r in recs if r[2] is not None and 0 <= r[2] < 80]
    rooms = [r[4] for r in recs if r[4] and 1 <= r[4] <= 6]
    # 房型分布
    by_type = {}
    tbuf = defaultdict(list)
    for r in recs: tbuf[r[3]].append(ping_price(r[1]))
    for bt, ps in tbuf.items():
        if len(ps) >= 15:
            by_type[TYPE_SHORT.get(bt, bt)] = {"n": len(ps), "median": round(statistics.median(ps), 1)}
    # 歷史趨勢：近 1 年 vs 前 1 年中位（factual，非預測）
    p_recent = [ping_price(r[1]) for r in recs if r[5] >= RECENT_1Y]
    p_prior  = [ping_price(r[1]) for r in recs if PRIOR_1Y_LO <= r[5] < PRIOR_1Y_HI]
    trend = None
    if len(p_recent) >= 20 and len(p_prior) >= 20:
        mr, mp = statistics.median(p_recent), statistics.median(p_prior)
        trend = round((mr - mp) / mp * 100, 1)
    room_mode = statistics.mode(rooms) if rooms else None
    districts.append({
        "name": dist,
        "n": len(prices),
        "median": round(statistics.median(prices), 1),
        "p25": round(pct(prices, 25), 1),
        "p75": round(pct(prices, 75), 1),
        "age_median": round(statistics.median(ages), 0) if ages else None,
        "room_mode": room_mode,
        "by_type": dict(sorted(by_type.items(), key=lambda x: -x[1]["n"])),
        "trend_1y_pct": trend,
        "n_recent_1y": len(p_recent),
    })

districts.sort(key=lambda x: -x["n"])
out = {
    "meta": {
        "source": "內政部不動產交易實價登錄",
        "data_range": "民國113年6月 ～ 115年第1季成交",
        "unit": "萬元/坪（中位數）",
        "generated": "2026-05-30",
        "method": "已排除車位、店面、廠辦及極端單價（<10 或 >148 萬/坪），採中位數較不受少數高價拉抬。",
        "disclaimer": "數據反映過去成交，僅供初步參考，不代表個別物件行情，亦不構成未來房價預測。實際以個案條件與估價為準。",
    },
    "districts": districts,
}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print(f"OK -> {OUT}")
print(f"區數: {len(districts)}  總樣本: {sum(d['n'] for d in districts)}")
for d in districts[:8]:
    print(f"  {d['name']:6s} n={d['n']:5d} 中位 {d['median']:5.1f} 萬/坪  "
          f"區間 {d['p25']}-{d['p75']}  屋齡{d['age_median']}  近1年趨勢 {d['trend_1y_pct']}%")
