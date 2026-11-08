# 我的台股日報

每個交易日收盤後（台灣時間 15:30），GitHub Actions 會自動執行 `analyze.py`：

1. 從證交所 / 櫃買中心取得當日成交金額前 400 名的股票
2. 用 yfinance 下載一年股價，計算均線、RSI、KD、MACD、ATR、成交量
3. 依規則評分，挑出可留意的進場標的，並分析你的持股
4. 追蹤影響資金面的數據（`macro.py`）：
   - 國際市場：美債 10 年 / 5 年 / 3 個月殖利率、WTI / 布蘭特原油、黃金、美元指數、美元台幣、VIX、標普、那斯達克、費半（yfinance）
   - 美國總經：CPI、核心 CPI、非農新增就業、失業率（BLS 官方 API）
   - 財經日曆：FOMC 會議（聯準會官網）、美股科技巨頭與台股大廠財報 / 法說日期、台股營收公布日
   - 月營收：持股與科技大廠的月增 / 年增（證交所、櫃買中心 OpenAPI）
   - 新聞：川普政策、CPI / 非農 / 聯準會、美債原油美元、科技大廠法說、持股相關（Google News RSS）
5. 結果寫入 `docs/data.json`，由 GitHub Pages 顯示在 `docs/index.html`，並開 Issue 寄 Email 日報

## 盤中訊號提醒

交易時段（09:07～13:37）每 15 分鐘，GitHub Actions 會執行 `intraday.py`：

- 用證交所即時報價（延遲約 5 秒）代入今天的 K 棒，重算均線、KD、MACD、RSI
- **持股**：出現出場訊號（跌破月線、跌破參考停損、死亡交叉、MACD 翻空、RSI 過熱、盤中急跌、虧損達停損線）→ 🔴 出場警示；出現多方訊號 → 🔵 加碼參考
- **昨日推薦標的 + 觀察清單**：出現進場訊號（突破 20 日高、黃金交叉、MACD 翻多、帶量上漲）且分數達 `min_score` → 🟢 進場訊號
- 同一檔的同一個訊號一天只提醒一次；提醒會留言在當天的「盤中訊號」Issue，GitHub 會寄 Email。
  手機安裝 GitHub App 並開啟通知，可以收到即時推播
- 網頁最上方會顯示當天的盤中訊號

`config.json` 的 `intraday`：`drop_pct` 急跌警示 %、`include_picks` 是否監控昨日推薦標的。
GitHub 排程在尖峰時段可能延遲幾分鐘。

CPI、非農的「公布日期」無法自動抓取（BLS 網站阻擋程式存取），可在 `config.json` 的 `manual_events` 手動加入：
`{"date": "2026-12-10", "event": "美國 11 月 CPI 公布（台灣時間 21:30）"}`。
新聞關鍵字可在 `news_topics` 調整。

## 設定持股（不公開成本）

到 repo 的 **Settings → Secrets and variables → Actions → New repository secret**，
名稱填 `HOLDINGS`，內容填：

```json
[{"code": "2330", "cost": 900}, {"code": "2317", "cost": 180}]
```

網頁只會顯示代號、訊號與報酬率，不會顯示成本。

## 調整參數

`config.json`：`watchlist` 觀察清單、`min_trade_value` 成交金額門檻、`min_score` 推薦分數門檻、
`top_picks` 推薦檔數、`stop_loss_pct` 停損警示 %。

## 本機執行

```
pip install -r requirements.txt
python analyze.py          # 持股可放在 holdings.json（已被 .gitignore 排除）
python -m http.server 8765 --directory docs
```

僅供學習參考，不構成投資建議。
