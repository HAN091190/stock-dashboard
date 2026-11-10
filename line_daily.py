"""把 docs/data.json 做成 LINE Flex 卡片日報（左右滑動的 4 張卡），推播給自己。

用法：python line_daily.py [docs/data.json]
需要環境變數 LINE_CHANNEL_ACCESS_TOKEN、LINE_USER_ID；設 DRY_RUN=1 只輸出 JSON 不推播。
卡片：① 大盤＋持股 ② 國際・總經・行事曆 ③ 進場標的 ④ 新聞重點（點標題開新聞，不顯示網址）。
Flex 推播失敗時自動改送精簡純文字版（不含網址）。
"""
import json
import os
import re
import sys
from pathlib import Path

from notify_line import SITE, push

ROOT = Path(__file__).parent
UP, DOWN, FLAT = "#D93025", "#1E8E3E", "#888888"  # 台股慣例：紅漲綠跌
MUTED, LINK, HEAD = "#8A8A8A", "#1A5FB4", "#1F3B73"
NEWS_PER_TOPIC = 2
NEWS_HOLDINGS = 4

SHORT = {
    "美國 10 年期公債殖利率": "美債 10 年",
    "美國 5 年期公債殖利率": "美債 5 年",
    "美國 3 個月國庫券殖利率": "美債 3 個月",
    "VIX 恐慌指數": "VIX",
}


# ---------- 小工具 ----------
def num(v):
    s = f"{v:,.2f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def color(v):
    return UP if v > 0 else DOWN if v < 0 else FLAT


def pct(v):
    return f"{v:+.2f}%"


def tag(sig):
    """'空頭排列（股價 < 月線 < 季線）' → '空頭排列'"""
    return re.split(r"[（(]", sig)[0].strip()


def verdict_color(v):
    return UP if "多" in v else DOWN if "弱" in v or "空" in v else FLAT


def T(text, size="sm", **kw):
    return {"type": "text", "text": str(text) or " ", "size": size, **kw}


def row(*cells, margin="md"):
    return {"type": "box", "layout": "horizontal", "contents": list(cells), "margin": margin, "spacing": "sm"}


def title(text):
    return T(text, "md", weight="bold", color=HEAD, margin="xl")


def sep():
    return {"type": "separator", "margin": "lg"}


def bubble(head, sub, body, button=("看完整網頁", SITE)):
    return {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box", "layout": "vertical", "backgroundColor": HEAD, "paddingAll": "lg",
            "contents": [T(head, "lg", weight="bold", color="#FFFFFF"), T(sub, "xs", color="#C8D3EA")],
        },
        "body": {"type": "box", "layout": "vertical", "contents": body, "paddingAll": "lg"},
        "footer": {
            "type": "box", "layout": "vertical", "paddingAll": "sm",
            "contents": [{"type": "button", "style": "link", "height": "sm",
                          "action": {"type": "uri", "label": button[0], "uri": button[1]}}],
        },
    }


def stock_rows(r, extra):
    """兩行：名稱 / 收盤 / 漲跌 / 評等，下面一行灰字小註。"""
    return [
        row(
            T(f"{r['code']} {r['name']}", weight="bold", flex=5, wrap=True),
            T(num(r["close"]), flex=3, align="end"),
            T(pct(r["change_pct"]), flex=3, align="end", color=color(r["change_pct"])),
            extra,
        ),
        T("・".join(filter(None, [f"停損 {num(r['stop'])}"] + [tag(s) for s in r["bull"] + r["bear"]][:3])),
          "xxs", color=MUTED, wrap=True, margin="xs"),
    ]


# ---------- 四張卡 ----------
def card_holdings(d, day):
    body = []
    m = d.get("market")
    if m:
        body += [
            row(T("加權指數", weight="bold", flex=4),
                T(f"{m['close']:,.2f}", flex=4, align="end"),
                T(pct(m["change_pct"]), flex=3, align="end", color=color(m["change_pct"]), weight="bold"),
                margin="none"),
            T(f"{m['verdict']}・" + "・".join(tag(s) for s in m["bull"] + m["bear"]) if m["bull"] + m["bear"]
              else m["verdict"], "xs", color=verdict_color(m["verdict"]), wrap=True, margin="xs"),
            sep(),
        ]
    body.append(title("我的持股"))
    hs = d.get("holdings") or []
    if not hs:
        body.append(T("尚未設定持股", color=MUTED, margin="md"))
    for r in hs:
        right = (T(pct(r["return_pct"]), "xs", flex=2, align="end", color=color(r["return_pct"]))
                 if "return_pct" in r else
                 T(r["verdict"], "xs", flex=2, align="end", color=verdict_color(r["verdict"]), weight="bold"))
        body += stock_rows(r, right)
    if hs:
        up = sum(r["change_pct"] > 0 for r in hs)
        down = sum(r["change_pct"] < 0 for r in hs)
        body.append(T(f"持股 {len(hs)} 檔：漲 {up}・跌 {down}・平 {len(hs) - up - down}", "xs",
                      color=MUTED, margin="lg", align="end"))
    return bubble(f"📈 台股日報 {day}", f"更新 {d['updated']}（1/4 持股・左滑看更多）", body)


def card_macro(d, day):
    body = [title("國際市場")]
    body[0]["margin"] = "none"
    for x in d.get("global") or []:
        if x["kind"] == "yield":
            val, chg, c = f"{x['value']:.3f}%", f"{x['chg']:+.1f}bp", color(x["chg"])
        else:
            val, chg, c = num(x["value"]), pct(x["chg"]), color(x["chg"])
        body.append(row(T(SHORT.get(x["name"], x["name"]), flex=5),
                        T(val, flex=4, align="end"), T(chg, flex=3, align="end", color=c), margin="sm"))
    u = d.get("us_macro") or []
    if u:
        body += [sep(), title("美國經濟數據")]
        for x in u:
            body.append(row(T(x["name"], flex=5, wrap=True), T(x["value"], flex=3, align="end", weight="bold"),
                            T(f"前 {x['prev']}", "xs", flex=3, align="end", color=MUTED, gravity="center"),
                            margin="sm"))
    cal = (d.get("calendar") or [])[:6]
    if cal:
        body += [sep(), title("近期行事曆")]
        for e in cal:
            body.append(row(T(e["date"][5:].replace("-", "/"), "xs", flex=2, color=MUTED),
                            T(e["event"], "xs", flex=8, wrap=True), margin="sm"))
    return bubble("🌏 國際・總經", f"{day}（2/4）", body)


def card_picks(d, day):
    ps = d.get("picks") or []
    body = [T("技術面分數最高的標的，非投資建議", "xxs", color=MUTED)]
    if not ps:
        body.append(T("今天沒有符合條件的標的", color=MUTED, margin="lg"))
    for r in ps:
        body += stock_rows(r, T(f"{r['score']:+d}分", "xs", flex=2, align="end", color=UP, weight="bold"))
    return bubble("🎯 可留意的進場標的", f"{day}（3/4）", body)


def card_news(d, day):
    body = [T("點標題直接開啟新聞", "xxs", color=MUTED)]
    for sec in d.get("news") or []:
        n = NEWS_HOLDINGS if "持股" in sec["topic"] else NEWS_PER_TOPIC
        items = [x for x in sec["items"] if x.get("link", "").startswith("http") and len(x["link"]) <= 1000][:n]
        if not items:
            continue
        body.append(title(sec["topic"]))
        for x in items:
            body.append({
                "type": "box", "layout": "vertical", "margin": "md",
                "action": {"type": "uri", "label": "開啟", "uri": x["link"]},
                "contents": [T(x["title"], color=LINK, wrap=True, maxLines=2),
                             T(f"{x.get('source', '')} {x.get('time', '')}".strip() or " ", "xxs", color=MUTED)],
            })
    body.append(T("依技術指標與公開資料自動產生，僅供參考，不構成投資建議。", "xxs", color=MUTED, wrap=True,
                  margin="xl"))
    return bubble("📰 新聞重點", f"{day}（4/4）", body, ("看全部新聞", SITE + "#news"))


# ---------- 組合 ----------
def alt_text(d, day):
    parts = [f"台股日報 {day}"]
    m = d.get("market")
    if m:
        parts.append(f"加權 {m['close']:,.0f}（{pct(m['change_pct'])}）{m['verdict']}")
    hs = d.get("holdings") or []
    if hs:
        parts.append(f"持股 {len(hs)} 檔 漲{sum(r['change_pct'] > 0 for r in hs)} 跌{sum(r['change_pct'] < 0 for r in hs)}")
    return "｜".join(parts)[:400]


def flex_message(d):
    day = d["updated"][5:10].replace("-", "/")
    cards = [card_holdings(d, day), card_macro(d, day), card_picks(d, day), card_news(d, day)]
    return {"type": "flex", "altText": alt_text(d, day), "contents": {"type": "carousel", "contents": cards}}


def text_message(d):
    """Flex 失敗時的備援：精簡純文字、不放網址。"""
    day = d["updated"][5:10].replace("-", "/")
    L = [f"📈 台股日報 {day}"]
    m = d.get("market")
    if m:
        L.append(f"加權 {m['close']:,.2f}（{pct(m['change_pct'])}）{m['verdict']}")
    L += ["", "【我的持股】"]
    for r in d.get("holdings") or []:
        L.append(f"{r['code']} {r['name']} {num(r['close'])} {pct(r['change_pct'])} {r['verdict']}")
    g = d.get("global") or []
    if g:
        L += ["", "【國際】"]
        for x in g:
            v = f"{x['value']:.3f}% {x['chg']:+.1f}bp" if x["kind"] == "yield" else f"{num(x['value'])} {pct(x['chg'])}"
            L.append(f"{SHORT.get(x['name'], x['name'])} {v}")
    ps = d.get("picks") or []
    if ps:
        L += ["", "【進場標的】"] + [f"{r['code']} {r['name']} {num(r['close'])} {r['score']:+d}分" for r in ps]
    L += ["", f"完整日報與新聞：{SITE}"]
    return {"type": "text", "text": "\n".join(L)[:4900]}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "data.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    flex = flex_message(data)
    if os.environ.get("DRY_RUN"):
        print(json.dumps(flex, ensure_ascii=False, indent=1))
        print(f"\nFlex 大小 {len(json.dumps(flex, ensure_ascii=False).encode()) / 1024:.1f} KB", file=sys.stderr)
        print(text_message(data)["text"], file=sys.stderr)
    elif not push([flex], exit_on_fail=False):
        print("Flex 卡片推播失敗，改送純文字版", file=sys.stderr)
        push([text_message(data)])
