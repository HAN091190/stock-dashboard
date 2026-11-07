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
