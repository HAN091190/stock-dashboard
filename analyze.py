"""台股每日技術分析：分析持股 + 篩選可能的進場標的，輸出 docs/data.json。

僅供學習參考，不構成投資建議。
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).parent
TZ_TW = timezone(timedelta(hours=8))
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


# ---------- 資料取得 ----------

def get_universe():
    """取得上市 + 上櫃普通股清單（4 碼代號）與當日成交金額。"""
    stocks = {}
    try:
        r = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=30)
        r.encoding = "utf-8"
        for s in r.json():
            code = s.get("Code", "")
            if re.fullmatch(r"\d{4,6}[A-Z]?", code):
                stocks[code] = {"name": s["Name"].strip(), "suffix": ".TW",
                                "value": float(s.get("TradeValue") or 0)}
    except Exception as e:
        print("TWSE 清單取得失敗:", e, file=sys.stderr)
    try:
        r = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", timeout=30)
        r.encoding = "utf-8"
        for s in r.json():
            code = s.get("SecuritiesCompanyCode", "")
            if re.fullmatch(r"\d{4,6}[A-Z]?", code):
                try:
                    value = float(s.get("TransactionAmount") or 0)
                except ValueError:
                    value = 0
                stocks[code] = {"name": s["CompanyName"].strip(), "suffix": ".TWO", "value": value}
    except Exception as e:
        print("TPEx 清單取得失敗:", e, file=sys.stderr)
    return stocks


def load_holdings():
    """持股來源：環境變數 HOLDINGS（GitHub Secret）優先，否則讀本機 holdings.json。"""
    raw = os.environ.get("HOLDINGS")
    if not raw:
        p = ROOT / "holdings.json"
        raw = p.read_text(encoding="utf-8") if p.exists() else "[]"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print("持股格式錯誤，請確認是 JSON 陣列", file=sys.stderr)
        return []


def download(tickers):
    frames = []
    for i in range(0, len(tickers), 100):  # 分批下載，避免一次請求太多
        frames.append(yf.download(tickers[i:i + 100], period="1y", interval="1d", group_by="ticker",
                                  auto_adjust=False, threads=False, progress=False))
    data = pd.concat(frames, axis=1)
    out = {}
    for t in tickers:
        try:
            df = data[t].dropna(subset=["Close"])
            if len(df) >= 70:
                out[t] = df
        except KeyError:
            pass
    return out


# ---------- 技術指標 ----------

def indicators(df):
    d = pd.DataFrame(index=df.index)
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    d["close"], d["volume"] = c, v
    for n in (5, 20, 60):
        d[f"ma{n}"] = c.rolling(n).mean()
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    d["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    low9, high9 = l.rolling(9).min(), h.rolling(9).max()
    rsv = (c - low9) / (high9 - low9).replace(0, np.nan) * 100
    d["k"] = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d["d"] = d["k"].ewm(alpha=1 / 3, adjust=False).mean()
    ema12, ema26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    d["macd"] = ema12 - ema26
    d["signal"] = d["macd"].ewm(span=9, adjust=False).mean()
    d["hist"] = d["macd"] - d["signal"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(14).mean()
    d["vol20"] = v.rolling(20).mean()
    d["high20"] = h.rolling(20).max().shift(1)
    return d


def crossed_up(a, b, days=3):
    """a 在最近 days 天內由下往上穿越 b。"""
    diff = (a - b).iloc[-(days + 1):]
    return bool((diff.iloc[:-1] <= 0).any() and diff.iloc[-1] > 0)


def crossed_down(a, b, days=3):
    diff = (a - b).iloc[-(days + 1):]
    return bool((diff.iloc[:-1] >= 0).any() and diff.iloc[-1] < 0)


def evaluate(df):
    d = indicators(df)
    x = d.iloc[-1]
    prev_close = d["close"].iloc[-2]
    score, bull, bear = 0, [], []

    if x.close > x.ma20 > x.ma60:
        score += 2; bull.append("多頭排列（股價 > 月線 > 季線）")
    elif x.close < x.ma20 < x.ma60:
        score -= 2; bear.append("空頭排列（股價 < 月線 < 季線）")
    if crossed_up(d["ma5"], d["ma20"]):
        score += 2; bull.append("5 日線黃金交叉月線")
    if crossed_down(d["ma5"], d["ma20"]):
        score -= 2; bear.append("5 日線死亡交叉月線")
    if crossed_up(d["hist"], pd.Series(0, index=d.index)):
        score += 2; bull.append("MACD 翻多")
    if crossed_down(d["hist"], pd.Series(0, index=d.index)):
        score -= 1; bear.append("MACD 翻空")
    if crossed_up(d["k"], d["d"]) and d["k"].iloc[-4:].min() < 30:
        score += 2; bull.append("KD 低檔黃金交叉")
    if crossed_down(d["k"], d["d"]) and d["k"].iloc[-4:].max() > 80:
        score -= 1; bear.append("KD 高檔死亡交叉")
    if 45 <= x.rsi <= 65:
        score += 1; bull.append(f"RSI {x.rsi:.0f} 健康區間")
    elif x.rsi >= 75:
        score -= 2; bear.append(f"RSI {x.rsi:.0f} 過熱")
    elif x.rsi <= 25:
        bear.append(f"RSI {x.rsi:.0f} 超賣（留意止跌訊號）")
    if x.close > x.high20:
        score += 2; bull.append("突破 20 日高點")
    if x.volume > 1.5 * x.vol20 and x.close > prev_close:
        score += 1; bull.append("帶量上漲（量 > 1.5 倍均量）")
    if x.close < x.ma20 and prev_close >= d["ma20"].iloc[-2]:
        score -= 2; bear.append("跌破月線")
    if (x.close - x.ma20) / x.ma20 > 0.15:
        score -= 1; bear.append("乖離月線過大（> 15%）")

    if score >= 5:
        verdict = "偏多"
    elif score >= 2:
        verdict = "中性偏多"
    elif score >= -1:
        verdict = "中性"
    else:
        verdict = "偏弱"

    hist = d["close"].iloc[-60:]
    return {
        "date": d.index[-1].strftime("%Y-%m-%d"),
        "close": round(float(x.close), 2),
        "change_pct": round(float((x.close / prev_close - 1) * 100), 2),
        "ma20": round(float(x.ma20), 2),
        "ma60": round(float(x.ma60), 2),
        "rsi": round(float(x.rsi), 1),
        "k": round(float(x.k), 1),
        "d": round(float(x.d), 1),
        "stop": round(float(max(x.close - 2 * x.atr, 0)), 2),
        "score": score,
        "verdict": verdict,
        "bull": bull,
        "bear": bear,
        "spark": [round(float(v), 2) for v in hist],
    }


# ---------- 主程式 ----------

def main():
    universe = get_universe()
    holdings = load_holdings()
    cfg = CONFIG
    print(f"股票清單: {len(universe)} 檔，持股: {len(holdings)} 檔")

    def ticker(code):
        info = universe.get(code)
        return code + (info["suffix"] if info else ".TW")

    # 篩選候選：成交金額達門檻的前 N 名
    liquid = sorted(
        (c for c, i in universe.items()
         if re.fullmatch(r"[1-9]\d{3}", c) and i["value"] >= cfg["min_trade_value"]),
        key=lambda c: universe[c]["value"], reverse=True,
    )[: cfg["max_candidates"]]

    codes = set(liquid) | {str(h["code"]) for h in holdings} | set(map(str, cfg.get("watchlist", [])))
    tickers = [ticker(c) for c in codes] + ["^TWII"]
    print(f"下載 {len(tickers)} 檔歷史資料…")
    prices = download(tickers)

    results = {}
    for c in codes:
        df = prices.get(ticker(c))
        if df is None:
            continue
        try:
            r = evaluate(df)
            r["code"] = c
            r["name"] = universe.get(c, {}).get("name", c)
            results[c] = r
        except Exception as e:
            print(c, "分析失敗:", e, file=sys.stderr)

    my = []
    for h in holdings:
        c = str(h["code"])
        if c not in results:
            continue
        r = dict(results[c])
        cost = h.get("cost")
        if cost:
            r["return_pct"] = round((r["close"] / float(cost) - 1) * 100, 2)
            if r["return_pct"] <= -cfg["stop_loss_pct"]:
                r["bear"] = r["bear"] + [f"虧損已達 {cfg['stop_loss_pct']}% 停損線"]
        my.append(r)

    picks = sorted(
        (results[c] for c in liquid if c in results and results[c]["score"] >= cfg["min_score"]),
        key=lambda r: (r["score"], r["change_pct"]), reverse=True,
    )[: cfg["top_picks"]]
    watch = [results[str(c)] for c in cfg.get("watchlist", []) if str(c) in results]

    market = None
    if "^TWII" in prices:
        m = evaluate(prices["^TWII"])
        market = {k: m[k] for k in ("close", "change_pct", "ma20", "ma60", "rsi", "verdict", "bull", "bear", "spark")}

    out = {
        "updated": datetime.now(TZ_TW).strftime("%Y-%m-%d %H:%M"),
        "scanned": len([c for c in liquid if c in results]),
        "market": market,
        "holdings": my,
        "picks": picks,
        "watchlist": watch,
    }
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "data.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"完成：掃描 {out['scanned']} 檔，推薦 {len(picks)} 檔")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
