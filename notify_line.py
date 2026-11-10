"""把 Markdown 報告轉成 LINE 純文字，用 LINE Messaging API 推播給自己。

用法：python notify_line.py <檔案.md> [標題]
需要環境變數 LINE_CHANNEL_ACCESS_TOKEN、LINE_USER_ID。
LINE 單則文字上限 5000 字、一次推播最多 5 則（只算 1 則額度），超過會截斷並附網頁連結。
"""
import os
import re
import sys

import requests

SITE = "https://han091190.github.io/stock-dashboard/"
MAX_CHARS = 4900
MAX_BUBBLES = 5


def md_to_text(md):
    out = []
    header = None
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(re.fullmatch(r":?-+:?", c) for c in cells if c):
                continue  # 表格分隔線
            if header is None:
                header = cells
                continue  # 表頭不印，避免訊息過長
            pairs = list(zip(header, cells))
            first = cells[0]
            if not header[0] and len(cells) > 1:  # 盤中訊號表第一欄是圖示、無表頭
                first = f"{cells[0]}｜{cells[1]}"
                pairs = pairs[1:]
            rest = [f"{h} {c}" if h else c for h, c in pairs[1:] if c and c != "—"]
            short = [r for r in rest if len(r) <= 20]
            long_ = [r for r in rest if len(r) > 20]
            out.append(f"▸ {first}" + (f"\n   {' ｜ '.join(short)}" if short else "")
                       + "".join(f"\n   {r}" for r in long_))
            continue
        header = None
        s = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1", s)  # 連結只留文字，不放長網址
        s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
        m = re.match(r"(#{1,6})\s+(.*)", s)
        if m:
            out += ["", f"【{m.group(2)}】"]
            continue
        s = re.sub(r"^[-*]\s+", "・", s)
        out.append(s)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return text


def split_bubbles(text):
    """依段落切成多則，每則不超過 MAX_CHARS。"""
    bubbles, cur = [], ""
    for para in text.split("\n\n"):
        while len(para) > MAX_CHARS:
            bubbles.append(para[:MAX_CHARS])
            para = para[MAX_CHARS:]
        if len(cur) + len(para) + 2 > MAX_CHARS:
            bubbles.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        bubbles.append(cur)
    if len(bubbles) > MAX_BUBBLES:
        bubbles = bubbles[:MAX_BUBBLES]
        bubbles[-1] = bubbles[-1][:MAX_CHARS - 60] + f"\n\n…內容過長已截斷，完整內容：{SITE}"
    return bubbles


def push(messages, exit_on_fail=True):
    """messages：字串（純文字）或 LINE message 物件（例如 Flex），最多 5 則。"""
    token = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
    user = os.environ["LINE_USER_ID"]
    msgs = [{"type": "text", "text": m} if isinstance(m, str) else m for m in messages]
    r = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {token}"},
        json={"to": user, "messages": msgs},
        timeout=30,
    )
    if r.status_code != 200:
        print(f"LINE 推播失敗 {r.status_code}: {r.text}", file=sys.stderr)
        if exit_on_fail:
            sys.exit(1)
        return False
    print(f"LINE 推播成功，共 {len(msgs)} 則")
    return True


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    path = sys.argv[1]
    title = sys.argv[2] if len(sys.argv) > 2 else ""
    with open(path, encoding="utf-8") as f:
        text = md_to_text(f.read())
    if title:
        text = f"📈 {title}\n\n{text}"
    bubbles = split_bubbles(text)
    if os.environ.get("DRY_RUN"):
        for b in bubbles:
            print(b, "\n" + "=" * 40)
    else:
        push(bubbles)
