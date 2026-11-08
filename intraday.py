"""盤中訊號監控：用證交所即時報價（延遲約 5 秒）重算技術訊號，出現新的進場 / 出場訊號就提醒。

由 GitHub Actions 在交易時段每 15 分鐘執行一次。
- 持股：出現出場訊號（跌破月線、跌破停損、死亡交叉、急跌…）或加碼訊號就提醒
- 昨日推薦標的 + 觀察清單：出現進場訊號且分數達門檻就提醒
同一檔股票的同一個訊號，一天只提醒一次。結果寫入 docs/intraday.json，
有新訊號時另外寫 alert.md，供 workflow 寄出通知。

僅供學習參考，不構成投資建議。
"""
import json
import re
import sys
import time
from datetime import datetime

import pandas as pd
import requests

from analyze import CONFIG, ROOT, TZ_TW, download, evaluate, load_holdings

STATE = ROOT / "docs" / "intraday.json"
ALERT = ROOT / "alert.md"
OPT = {"drop_pct": 5, "include_picks": True, **CONFIG.get("intraday", {})}

EXIT_KEYS = ("跌破月線", "死亡交叉", "MACD 翻空", "空頭排列", "過熱", "停損", "急跌")
ENTRY_KEYS = ("突破 20 日高點", "黃金交叉", "MACD 翻多", "帶量上漲", "多頭排列")


def num(s):
    try:
        v = float(s)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def quotes(codes):
    """證交所 MIS 即時報價。代號不分上市上櫃，兩個市場都查，有回應的就是。"""
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://mis.twse.com.tw/stock/index.jsp"})
    try:
        s.get("https://mis.twse.com.tw/stock/index.jsp", timeout=15)
    except requests.RequestException:
        pass
    out = {}
    codes = list(codes)
    for i in range(0, len(codes), 20):
        chans = "|".join(f"{ex}_{c}.tw" for c in codes[i:i + 20] for ex in ("tse", "otc"))
        r = s.get("https://mis.twse.com.tw/stock/api/getStockInfo.jsp",
                  params={"ex_ch": chans, "json": "1", "delay": "0", "_": int(time.time() * 1000)}, timeout=20)
        for q in r.json().get("msgArray", []):
            if not q.get("c"):
                continue
            bid = (q.get("b") or "").split("_")[0]
            ask = (q.get("a") or "").split("_")[0]
            price = num(q.get("z")) or num((q.get("trade") or {}).get("z")) or num(q.get("pz")) or num(bid) or num(ask)
            prev = num(q.get("y"))
            if not price or not prev:
                continue
            out[q["c"]] = {
                "name": q.get("n", q["c"]), "suffix": ".TWO" if q.get("ex") == "otc" else ".TW",
                "price": price, "prev": prev, "open": num(q.get("o")) or price,
                "high": max(num(q.get("h")) or price, price), "low": min(num(q.get("l")) or price, price),
                "volume": (num(q.get("v")) or 0) * 1000,  # 張 → 股，與 yfinance 一致
                "date": q.get("d", ""), "time": q.get("t", ""),
            }
        time.sleep(0.5)
    return out


def key(signal):
    """去掉數字，讓「RSI 76 過熱」和「RSI 78 過熱」視為同一個訊號。"""
    return re.sub(r"[-+\d.,%]+", "", signal).strip()


def main(force=False):
    now = datetime.now(TZ_TW)
    today = now.strftime("%Y-%m-%d")
    minutes = now.hour * 60 + now.minute
    if not force and not (9 * 60 <= minutes <= 13 * 60 + 35):
        print("非交易時段，略過")
        return

    holdings = {str(h["code"]): h for h in load_holdings()}
    targets = list(dict.fromkeys(str(c) for c in CONFIG.get("watchlist", [])))
    daily = ROOT / "docs" / "data.json"
    if OPT["include_picks"] and daily.exists():
        targets = list(dict.fromkeys([p["code"] for p in json.loads(daily.read_text(encoding="utf-8"))["picks"]] + targets))
    targets = [c for c in targets if c not in holdings]

    q = quotes(list(holdings) + targets)
    if not q:
        print("取不到即時報價，略過")
        return
    if not force and not any(x["date"] == now.strftime("%Y%m%d") for x in q.values()):
        print("今天不是交易日，略過")
        return

    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    if state.get("date") != today:
        state = {"date": today, "sent": [], "alerts": []}
    sent = set(state["sent"])

    tickers = {c: c + q[c]["suffix"] for c in q}
    hist = download(list(tickers.values()))
    new_alerts, live = [], []
    for c, t in tickers.items():
        df = hist.get(t)
        if df is None:
            continue
        x = q[c]
        df = df[df.index.date < now.date()]  # 去掉 yfinance 可能已有的今日不完整 K 棒
        base = evaluate(df)
        bar = pd.DataFrame({"Open": [x["open"]], "High": [x["high"]], "Low": [x["low"]], "Close": [x["price"]],
                            "Adj Close": [x["price"]], "Volume": [x["volume"]]},
                           index=pd.DatetimeIndex([pd.Timestamp(now.date())]))
        r = evaluate(pd.concat([df, bar[df.columns.intersection(bar.columns)]]))
        name = x["name"]
        old = {key(s) for s in base["bull"] + base["bear"]}
        new_bull = [s for s in r["bull"] if key(s) not in old and any(k in s for k in ENTRY_KEYS)]
        new_bear = [s for s in r["bear"] if key(s) not in old and any(k in s for k in EXIT_KEYS)]

        h = holdings.get(c)
        row = {"code": c, "name": name, "price": x["price"], "change_pct": r["change_pct"], "score": r["score"],
               "verdict": r["verdict"], "ma20": r["ma20"], "stop": base["stop"], "holding": bool(h), "time": x["time"]}
        if h:
            if x["price"] <= base["stop"]:
                new_bear.append(f"跌破參考停損價 {base['stop']:,}")
            if r["change_pct"] <= -OPT["drop_pct"]:
                new_bear.append(f"盤中急跌 {r['change_pct']:+.2f}%")
            if h.get("cost"):
                row["return_pct"] = round((x["price"] / float(h["cost"]) - 1) * 100, 2)
                if row["return_pct"] <= -CONFIG["stop_loss_pct"]:
                    new_bear.append(f"虧損已達 {CONFIG['stop_loss_pct']}% 停損線（{row['return_pct']:+.2f}%）")
            action = "出場警示" if new_bear else "加碼參考" if new_bull else None
            signals = ["▼" + s for s in new_bear] + ["▲" + s for s in new_bull]
            live.append(row)
        else:
            ok = new_bull and r["score"] >= CONFIG["min_score"]
            action = "進場訊號" if ok else None
            signals = ["▲" + s for s in new_bull] if ok else []

        fresh = [s for s in signals if f"{c}|{key(s)}" not in sent]
        if action and fresh:
            sent |= {f"{c}|{key(s)}" for s in fresh}
            new_alerts.append({**row, "action": action, "signals": fresh, "at": now.strftime("%H:%M")})

    order = {"出場警示": 0, "進場訊號": 1, "加碼參考": 2}
    new_alerts.sort(key=lambda a: order[a["action"]])
    state.update({"updated": now.strftime("%Y-%m-%d %H:%M"), "sent": sorted(sent),
                  "alerts": new_alerts + state["alerts"], "holdings": live})
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")

    ALERT.unlink(missing_ok=True)
    if new_alerts:
        ALERT.write_text(alert_md(new_alerts, now), encoding="utf-8")
    print(f"{now:%H:%M} 檢查 {len(tickers)} 檔，新訊號 {len(new_alerts)} 則")


def alert_md(alerts, now):
    icon = {"出場警示": "🔴", "進場訊號": "🟢", "加碼參考": "🔵"}
    lines = [f"### {now:%H:%M} 盤中訊號", "", "| | 股票 | 現價 | 漲跌 | 評等 | 參考停損 | 新訊號 |",
             "|---|---|---:|---:|---|---:|---|"]
    for a in alerts:
        lines.append(f"| {icon[a['action']]} {a['action']} | {a['code']} {a['name']} | {a['price']:,} | "
                     f"{a['change_pct']:+.2f}% | {a['verdict']} ({a['score']:+d}) | {a['stop']:,} | {'、'.join(a['signals'])} |")
    lines += ["", "[開啟網頁](https://han091190.github.io/stock-dashboard/)・盤中訊號以即時價格計算，"
              "收盤前可能消失，僅供參考，不構成投資建議。"]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main(force="--force" in sys.argv)
