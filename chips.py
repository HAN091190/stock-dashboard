"""大盤籌碼面：成交金額、融資融券、三大法人現貨、台指期三大法人未平倉、選擇權 P/C 與外資買賣權。

資料來源（皆為官方公開資料）：
- 證交所 FMTQIK（每日成交金額、加權指數）、MI_MARGN（信用交易統計）、BFI82U（三大法人買賣金額）
- 期交所 futContractsDateDown（期貨三大法人）、pcRatioDown（P/C ratio）、callsAndPutsDateDown（選擇權三大法人）

每個項目獨立抓取，失敗只會略過該項目。最後依籌碼訊號給出「籌碼多空分數」，僅供參考。

融資資料約 21:00 才公布，15:30 的日報會顯示前一交易日的融資（網頁有標日期）。
單獨執行：python chips.py [docs/data.json]，只更新 data.json 裡的 chips 區塊（本機測試用）。
"""
import io
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

TZ_TW = timezone(timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (stock-dashboard personal research script)"}
ROOT = Path(__file__).parent
HISTORY_DAYS = 20

# 評分門檻（可在 config.json 的 "chips" 覆寫）
DEFAULTS = {
    "foreign_cash_bn": 50,      # 外資現貨買賣超 ±50 億
    "trust_cash_bn": 10,        # 投信買賣超 ±10 億
    "fut_chg_lots": 2000,       # 外資台指期淨未平倉單日變化 ±2,000 口
    "fut_heavy_short": -30000,  # 外資淨空單超過 3 萬口視為偏重
    "pc_high": 120,             # P/C 未平倉比 > 120% 支撐強
    "pc_low": 80,               # P/C 未平倉比 < 80% 支撐弱
    "volume_ratio": 1.2,        # 成交金額 > 5 日均量 1.2 倍視為放量
}


def safe(fn, *args, default=None):
    try:
        return fn(*args)
    except Exception as e:
        print(f"[chips.{fn.__name__}] 略過：{e}", file=sys.stderr)
        return default


def num(s):
    if s is None:
        return None
    s = str(s).replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def roc_to_iso(s):
    """'115/11/26' → '2026-11-26'"""
    y, m, d = re.findall(r"\d+", s)[:3]
    return f"{int(y) + 1911:04d}-{int(m):02d}-{int(d):02d}"


# ---------- 證交所 ----------

def twse(path, **params):
    r = requests.get(f"https://www.twse.com.tw/rwd/zh/{path}", params={**params, "response": "json"},
                     headers=UA, timeout=30)
    r.raise_for_status()
    time.sleep(1.2)  # 證交所限制每 5 秒約 3 次
    return r.json()


def all_rows(j):
    """新舊版回傳格式不同（data / tables[].data / creditList），一律攤平成資料列。"""
    rows = []

    def walk(x):
        if isinstance(x, list):
            if x and all(isinstance(r, list) for r in x):
                rows.extend(x)
            else:
                for v in x:
                    walk(v)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
    walk(j)
    return rows


def find_row(rows, keyword):
    return next((r for r in rows if r and keyword in str(r[0])), None)


def turnover(today):
    """近一個多月的每日成交金額（億元）與加權指數。"""
    out = {}
    first = today.replace(day=1)
    for month in (first - timedelta(days=1), today):
        j = twse("afterTrading/FMTQIK", date=month.strftime("%Y%m01"))
        for r in all_rows(j):
            if r and re.match(r"\d+/\d+/\d+", str(r[0])):
                out[roc_to_iso(r[0])] = {"value_bn": round(num(r[2]) / 1e8, 1), "index": num(r[4]),
                                         "index_chg": num(r[5])}
    if not out:
        raise RuntimeError("沒有成交資料")
    days = sorted(out)
    last = days[-1]
    vals = [out[d]["value_bn"] for d in days]
    avg5 = sum(vals[-6:-1]) / len(vals[-6:-1]) if len(vals) > 1 else vals[-1]
    return {"date": last, "value_bn": out[last]["value_bn"], "avg5_bn": round(avg5, 1),
            "index": out[last]["index"], "index_chg": out[last]["index_chg"],
            "history": [{"date": d, "value_bn": out[d]["value_bn"], "index_chg": out[d]["index_chg"]}
                        for d in days[-HISTORY_DAYS:]]}


def margin(today):
    """集中市場融資餘額（億元）與融券餘額（張）。晚上約 21:00 後才公布，抓不到就往前找最近一天。"""
    for back in range(0, 8):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        rows = all_rows(twse("marginTrading/MI_MARGN", date=d.strftime("%Y%m%d"), selectType="MS"))
        amt = find_row(rows, "融資金額")
        short = find_row(rows, "融券")
        if amt and num(amt[-1]):
            prev, now = num(amt[-2]), num(amt[-1])  # 前日餘額、今日餘額（仟元）
            res = {"date": d.isoformat(), "balance_bn": round(now / 1e5, 2),
                   "chg_bn": round((now - prev) / 1e5, 2)}
            if short and num(short[-1]) is not None:
                res["short_lots"] = int(num(short[-1]))
                res["short_chg"] = int(num(short[-1]) - num(short[-2]))
            return res
    raise RuntimeError("近一週查無融資資料")


def institutional(today):
    """三大法人現貨買賣超（億元）。約 15:00 公布。"""
    for back in range(0, 8):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        rows = all_rows(twse("fund/BFI82U", type="day", dayDate=d.strftime("%Y%m%d")))
        if not rows:
            continue
        net = lambda k: sum(num(r[3]) or 0 for r in rows if r and k in str(r[0]))
        foreign = net("外資")             # 外資及陸資 + 外資自營商
        trust = net("投信")
        dealer = net("自營商") - net("外資自營商")
        total = find_row(rows, "合計")
        return {"date": d.isoformat(), "foreign_bn": round(foreign / 1e8, 1), "trust_bn": round(trust / 1e8, 1),
                "dealer_bn": round(dealer / 1e8, 1),
                "total_bn": round((num(total[3]) if total else foreign + trust + dealer) / 1e8, 1)}
    raise RuntimeError("近一週查無三大法人資料")


# ---------- 期交所 ----------

def taifex_csv(path, today, **form):
    start = today - timedelta(days=40)
    r = requests.post(f"https://www.taifex.com.tw/cht/3/{path}", headers=UA, timeout=30,
                      data={"queryStartDate": start.strftime("%Y/%m/%d"),
                            "queryEndDate": today.strftime("%Y/%m/%d"), **form})
    r.raise_for_status()
    text = r.content.decode("cp950", errors="replace")
    if "日期" not in text[:200]:
        raise RuntimeError("期交所回傳非 CSV（可能查詢無資料或被阻擋）")
    df = pd.read_csv(io.StringIO(text), index_col=False, dtype=str)
    df.columns = [re.sub(r"\s", "", c) for c in df.columns]
    df = df.apply(lambda s: s.str.strip() if s.dtype == object else s)
    df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
    return df.dropna(subset=["日期"]).sort_values("日期")


def col(df, *keys):
    """找出名稱同時包含所有關鍵字的欄位。"""
    for c in df.columns:
        if all(k in c for k in keys):
            return c
    raise KeyError("/".join(keys))


def futures(today):
    """台指期（大台）三大法人多空未平倉口數。"""
    df = taifex_csv("futContractsDateDown", today, commodityId="TXF")
    name = col(df, "商品")
    tx = df[name].str.contains("臺股期貨|台股期貨", na=False)
    if tx.any():
        df = df[tx]
    who, L, S, N = col(df, "身份"), col(df, "多方未平倉口數"), col(df, "空方未平倉口數"), col(df, "未平倉口數淨額")
    dates = sorted(df["日期"].unique())
    out = {"date": pd.Timestamp(dates[-1]).date().isoformat()}

    def get(d, ident, c):
        r = df[(df["日期"] == d) & df[who].str.contains(ident, na=False)]
        return int(num(r.iloc[0][c])) if len(r) else None

    for key, ident in (("foreign", "外資"), ("trust", "投信"), ("dealer", "自營")):
        now = get(dates[-1], ident, N)
        prev = get(dates[-2], ident, N) if len(dates) > 1 else None
        out[key] = {"long": get(dates[-1], ident, L), "short": get(dates[-1], ident, S), "net": now,
                    "chg": (now - prev) if now is not None and prev is not None else None}
    out["history"] = [{"date": pd.Timestamp(d).date().isoformat(), "foreign_net": get(d, "外資", N)}
                      for d in dates[-HISTORY_DAYS:]]
    return out


def pc_ratio(today):
    df = taifex_csv("pcRatioDown", today)
    oi, vol = col(df, "未平倉", "比率"), col(df, "成交量", "比率")
    last = df.iloc[-1]
    return {"date": last["日期"].date().isoformat(), "oi_ratio": num(last[oi]), "vol_ratio": num(last[vol]),
            "history": [{"date": r["日期"].date().isoformat(), "oi_ratio": num(r[oi])}
                        for _, r in df.tail(HISTORY_DAYS).iterrows()]}


def options(today):
    """臺指選擇權外資買權、賣權未平倉淨額（口數、億元）。"""
    df = taifex_csv("callsAndPutsDateDown", today, commodityId="TXO")
    cp, who = col(df, "買賣權"), col(df, "身份")
    lots, amt = col(df, "未平倉口數", "淨額"), col(df, "未平倉契約金額", "淨額")
    d = df["日期"].max()
    day = df[(df["日期"] == d) & df[who].str.contains("外資", na=False)]
    res = {"date": d.date().isoformat()}
    for key, word in (("call", "買權"), ("put", "賣權")):
        r = day[day[cp].str.contains(word, na=False)]
        if len(r):
            res[key] = {"net_lots": int(num(r.iloc[0][lots])), "net_amt_bn": round(num(r.iloc[0][amt]) / 1e5, 2)}
    if "call" not in res or "put" not in res:
        raise RuntimeError("查無外資買賣權資料")
    return res


# ---------- 籌碼多空分數 ----------

def score(c, th):
    """每個訊號 +1 / -1，加總後給出傾向。只看籌碼，不是預測保證。"""
    bull, bear = [], []
    inst, fut, pc, opt, mg, tv = (c.get(k) for k in ("institutional", "futures", "pc", "options", "margin", "turnover"))
    if inst:
        f, t = inst["foreign_bn"], inst["trust_bn"]
        if f >= th["foreign_cash_bn"]:
            bull.append(f"外資現貨買超 {f:,.0f} 億")
        elif f <= -th["foreign_cash_bn"]:
            bear.append(f"外資現貨賣超 {-f:,.0f} 億")
        if t >= th["trust_cash_bn"]:
            bull.append(f"投信買超 {t:,.0f} 億")
        elif t <= -th["trust_cash_bn"]:
            bear.append(f"投信賣超 {-t:,.0f} 億")
    if fut and fut["foreign"]["net"] is not None:
        n, ch = fut["foreign"]["net"], fut["foreign"]["chg"]
        if ch is not None and ch >= th["fut_chg_lots"]:
            bull.append(f"外資期貨淨單增加 {ch:,} 口（空單回補或加多）")
        elif ch is not None and ch <= -th["fut_chg_lots"]:
            bear.append(f"外資期貨淨單減少 {-ch:,} 口（加空或多單了結）")
        if n <= th["fut_heavy_short"]:
            bear.append(f"外資期貨淨空單偏重 {-n:,} 口")
        elif n > 0:
            bull.append(f"外資期貨站在多方 {n:,} 口")
    if pc and pc["oi_ratio"] is not None:
        r = pc["oi_ratio"]
        if r >= th["pc_high"]:
            bull.append(f"P/C 未平倉比 {r:.0f}%，下方支撐強")
        elif r <= th["pc_low"]:
            bear.append(f"P/C 未平倉比 {r:.0f}%，下方支撐弱")
    if opt:
        diff = opt["call"]["net_amt_bn"] - opt["put"]["net_amt_bn"]
        if diff > 0.5:
            bull.append("外資選擇權偏多（買權淨金額 > 賣權）")
        elif diff < -0.5:
            bear.append("外資選擇權偏空（賣權淨金額 > 買權）")
    idx_chg = tv.get("index_chg") if tv else None
    # 融資約 21:00 才公布，15:30 執行時拿到的是前一交易日，改用同一天的指數漲跌來比對
    mg_idx = next((h["index_chg"] for h in (tv or {}).get("history", []) if mg and h["date"] == mg["date"]), None)
    if mg and mg_idx is not None:
        if mg_idx < 0 and mg["chg_bn"] > 0:
            bear.append(f"{mg['date'][5:]} 指數跌、融資增 {mg['chg_bn']:.0f} 億（散戶接刀，籌碼凌亂）")
        elif mg_idx > 0 and mg["chg_bn"] < 0:
            bull.append(f"{mg['date'][5:]} 指數漲、融資減 {-mg['chg_bn']:.0f} 億（籌碼沉澱）")
    if tv and idx_chg is not None and tv["avg5_bn"]:
        ratio = tv["value_bn"] / tv["avg5_bn"]
        if ratio >= th["volume_ratio"]:
            (bull if idx_chg > 0 else bear).append(
                f"{'放量上漲' if idx_chg > 0 else '放量下跌'}（成交額為 5 日均量 {ratio:.1f} 倍）")
    s = len(bull) - len(bear)
    verdict = "偏多" if s >= 3 else "中性偏多" if s >= 1 else "偏空" if s <= -3 else "中性偏空" if s <= -1 else "中性"
    return {"score": s, "verdict": verdict, "bull": bull, "bear": bear}


def outlook(chip_verdict, market):
    """籌碼傾向 + 技術面，合成下一個交易日的觀察傾向。"""
    if not market:
        return chip_verdict
    tech = market.get("verdict", "中性")
    t = 1 if "多" in tech else -1 if ("弱" in tech or "空" in tech) else 0
    c = 1 if "多" in chip_verdict else -1 if "空" in chip_verdict else 0
    return {2: "偏多", 1: "中性偏多", 0: "多空拉鋸", -1: "中性偏空", -2: "偏空"}[t + c]


def collect(cfg=None, market=None):
    th = {**DEFAULTS, **((cfg or {}).get("chips") or {})}
    today = datetime.now(TZ_TW).date()
    c = {
        "updated": datetime.now(TZ_TW).strftime("%Y-%m-%d %H:%M"),
        "turnover": safe(turnover, today),
        "margin": safe(margin, today),
        "institutional": safe(institutional, today),
        "futures": safe(futures, today),
        "pc": safe(pc_ratio, today),
        "options": safe(options, today),
    }
    c["signal"] = score(c, th)
    c["outlook"] = outlook(c["signal"]["verdict"], market)
    return c


def merge_history(prev, c):
    """融資與外資現貨沒有現成的歷史 API，沿用上一版 data.json 累積。"""
    hist = {h["date"]: h for h in (prev or [])}
    mg, inst = c.get("margin"), c.get("institutional")
    if mg:
        hist.setdefault(mg["date"], {"date": mg["date"]})["margin_bn"] = mg["balance_bn"]
    if inst:
        hist.setdefault(inst["date"], {"date": inst["date"]})["foreign_bn"] = inst["foreign_bn"]
    return [hist[d] for d in sorted(hist)][-HISTORY_DAYS:]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "data.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    chips = collect(cfg, data.get("market"))
    chips["history"] = merge_history((data.get("chips") or {}).get("history"), chips)
    data["chips"] = chips
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in chips.items() if k != "history"}, ensure_ascii=False, indent=1))
