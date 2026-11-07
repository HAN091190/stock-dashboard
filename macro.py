"""總經、國際市場、財經日曆、營收與新聞追蹤。每個區塊獨立，失敗只會略過該區塊。"""
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import pandas as pd
import requests
import yfinance as yf

UA = {"User-Agent": "stock-dashboard/1.0 (personal research script)"}
TZ_TW = timezone(timedelta(hours=8))


def safe(fn, *args, default=None):
    try:
        return fn(*args)
    except Exception as e:
        print(f"[{fn.__name__}] 略過：{e}", file=sys.stderr)
        return default


# ---------- 國際市場 / 利率 / 原物料 ----------

MARKETS = [
    ("^TNX", "美國 10 年期公債殖利率", "yield"),
    ("^FVX", "美國 5 年期公債殖利率", "yield"),
    ("^IRX", "美國 3 個月國庫券殖利率", "yield"),
    ("CL=F", "WTI 原油", "price"),
    ("BZ=F", "布蘭特原油", "price"),
    ("GC=F", "黃金", "price"),
    ("DX-Y.NYB", "美元指數", "price"),
    ("TWD=X", "美元 / 台幣", "price"),
    ("^VIX", "VIX 恐慌指數", "price"),
    ("^GSPC", "標普 500", "price"),
    ("^IXIC", "那斯達克", "price"),
    ("^SOX", "費城半導體", "price"),
]


def markets():
    data = yf.download([m[0] for m in MARKETS], period="1mo", progress=False, auto_adjust=False)["Close"]
    out = []
    for sym, name, kind in MARKETS:
        s = data[sym].dropna() if sym in data else pd.Series(dtype=float)
        if len(s) < 6:
            continue
        last, prev, wk = float(s.iloc[-1]), float(s.iloc[-2]), float(s.iloc[-6])
        item = {"name": name, "value": round(last, 3 if kind == "yield" else 2), "kind": kind,
                "date": s.index[-1].strftime("%m/%d")}
        if kind == "yield":  # 殖利率用基點（bp）表示變化
            item["chg"] = round((last - prev) * 100, 1)
            item["chg5"] = round((last - wk) * 100, 1)
        else:
            item["chg"] = round((last / prev - 1) * 100, 2)
            item["chg5"] = round((last / wk - 1) * 100, 2)
        out.append(item)
    return out


# ---------- 美國總經數據（BLS 官方 API） ----------

def bls_series(ids):
    """美國勞工統計局官方公開 API（v2 無金鑰：每日 25 次，足夠使用）。"""
    r = requests.post("https://api.bls.gov/publicAPI/v2/timeseries/data/", json={"seriesid": ids},
                      headers={**UA, "Content-type": "application/json"}, timeout=30)
    r.raise_for_status()
    j = r.json()
    if j.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(j.get("message"))
    out = {}
    for s in j["Results"]["series"]:
        rows = [(f"{d['year']}-{d['period'][1:]}", float(d["value"])) for d in s["data"]
                if d["period"].startswith("M") and d["period"] != "M13" and d["value"] not in ("-", "")]
        out[s["seriesID"]] = pd.Series(dict(sorted(rows)))
    return out


def us_macro():
    s = bls_series(["CUUR0000SA0", "CUUR0000SA0L1E", "CES0000000001", "LNS14000000"])
    out = []
    for sid, name in (("CUUR0000SA0", "CPI 年增率"), ("CUUR0000SA0L1E", "核心 CPI 年增率")):
        v = s[sid]
        yoy = (v.iloc[-1] / v.iloc[-13] - 1) * 100
        prev = (v.iloc[-2] / v.iloc[-14] - 1) * 100
        out.append({"name": name, "value": f"{yoy:.1f}%", "prev": f"{prev:.1f}%", "period": v.index[-1]})
    v = s["CES0000000001"].diff()
    out.append({"name": "非農新增就業", "value": f"{v.iloc[-1]:+,.0f} 千人", "prev": f"{v.iloc[-2]:+,.0f} 千人",
                "period": v.index[-1]})
    v = s["LNS14000000"]
    out.append({"name": "失業率", "value": f"{v.iloc[-1]:.1f}%", "prev": f"{v.iloc[-2]:.1f}%", "period": v.index[-1]})
    return out


# ---------- 財經日曆 ----------

MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                      "September", "October", "November", "December"], 1)}


def fomc_dates():
    html = requests.get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm", headers=UA, timeout=20).text
    text = " ".join(re.sub(r"<[^>]+>", " ", html).split())
    dates = []
    for year in (date.today().year, date.today().year + 1):
        m = re.search(rf"{year} FOMC Meetings(.*?)(?:{year - 1} FOMC Meetings|$)", text)
        if not m:
            continue
        for mon, d1, d2 in re.findall(r"(January|February|March|April|May|June|July|August|September|October|"
                                      r"November|December)(?:/\w+)?\s+(\d{1,2})-(\d{1,2})", m.group(1)):
            end_month = MONTHS[mon] + (1 if int(d2) < int(d1) else 0)
            try:
                dates.append(date(year + (end_month > 12), (end_month - 1) % 12 + 1, int(d2)))
            except ValueError:
                pass
    return sorted(set(dates))


def calendar(cfg, tw_syms):
    """tw_syms: [(yfinance 代號, 名稱)]，台股要分 .TW / .TWO。"""
    today = datetime.now(TZ_TW).date()
    horizon = today + timedelta(days=cfg.get("calendar_days", 30))
    events = []

    for d in safe(fomc_dates, default=[]) or []:
        if today <= d <= horizon:
            events.append({"date": d.isoformat(), "event": "FOMC 利率決策（台灣時間隔天凌晨公布）", "type": "macro"})

    # 美股 / 台股重要公司財報（法說）日期
    syms = list(cfg.get("earnings_us", {}).items())
    syms += tw_syms
    seen = set()
    for sym, name in syms:
        if sym in seen:
            continue
        seen.add(sym)
        try:
            ed = yf.Ticker(sym).calendar.get("Earnings Date") or []
        except Exception:
            continue
        for d in ed[:1]:
            if isinstance(d, datetime):
                d = d.date()
            if today <= d <= horizon:
                events.append({"date": d.isoformat(), "event": f"{name} 財報 / 法說會", "type": "earnings"})

    # 每月固定事件
    for i in range(2):
        m = (today.replace(day=1) + timedelta(days=32 * i)).replace(day=10)
        if today <= m <= horizon:
            events.append({"date": m.isoformat(), "event": "台股上市櫃公司公布上月營收截止日", "type": "revenue"})

    for e in cfg.get("manual_events", []):  # 可在 config.json 手動加入（如 CPI、非農公布日）
        if today.isoformat() <= e["date"] <= horizon.isoformat():
            events.append({**e, "type": e.get("type", "macro")})

    return sorted(events, key=lambda e: e["date"])


# ---------- 台股月營收 ----------

def revenue(codes):
    rows = []
    for url in ("https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
                "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"):
        try:
            r = requests.get(url, headers=UA, timeout=30)
            r.encoding = "utf-8"
            rows += r.json()
        except Exception as e:
            print("營收資料取得失敗:", e, file=sys.stderr)
    by = {s.get("公司代號"): s for s in rows}
    out = []
    num = lambda v: float(v) if v not in (None, "") else None
    for c in codes:
        s = by.get(c)
        if not s:
            continue
        ym = s["資料年月"]
        out.append({
            "code": c, "name": s["公司名稱"],
            "period": f"{int(ym[:-2]) + 1911}-{ym[-2:]}",
            "revenue": round(float(s["營業收入-當月營收"]) / 1e5, 1),  # 千元 → 億元
            "mom": round(num(s["營業收入-上月比較增減(%)"]) or 0, 1),
            "yoy": round(num(s["營業收入-去年同月增減(%)"]) or 0, 1),
            "ytd_yoy": round(num(s["累計營業收入-前期比較增減(%)"]) or 0, 1),
            "note": (s.get("備註") or "").strip(" -"),
        })
    return out


# ---------- 新聞 ----------

def google_news(query, limit):
    r = requests.get("https://news.google.com/rss/search",
                     params={"q": query, "hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"}, headers=UA, timeout=20)
    r.raise_for_status()
    items = []
    for it in ET.fromstring(r.content).iter("item"):
        title = it.findtext("title") or ""
        src = it.findtext("source") or ""
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        try:
            t = parsedate_to_datetime(it.findtext("pubDate")).astimezone(TZ_TW).strftime("%m/%d %H:%M")
        except Exception:
            t = ""
        items.append({"title": title, "link": it.findtext("link"), "source": src, "time": t})
    items.sort(key=lambda x: x["time"], reverse=True)
    # 去除標題幾乎相同的重複新聞
    seen, uniq = set(), []
    for x in items:
        k = re.sub(r"\W", "", x["title"])[:18]
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    return uniq[:limit]


def news(cfg, holdings):
    out = []
    for topic in cfg.get("news_topics", []):
        items = safe(google_news, topic["query"], topic.get("limit", 5), default=[])
        if items:
            out.append({"topic": topic["name"], "items": items})
    per = []
    for h in holdings:
        items = safe(google_news, f'"{h["name"]}" (法說會 OR 營收 OR 財報) when:3d', 2, default=[])
        for x in items or []:
            per.append({**x, "title": f"【{h['name']}】{x['title']}"})
    if per:
        out.append({"topic": "我的持股相關新聞", "items": per})
    return out
