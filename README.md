# 我的台股日報

每個交易日收盤後（台灣時間 15:30），GitHub Actions 會自動執行 `analyze.py`：

1. 從證交所 / 櫃買中心取得當日成交金額前 400 名的股票
2. 用 yfinance 下載一年股價，計算均線、RSI、KD、MACD、ATR、成交量
3. 依規則評分，挑出可留意的進場標的，並分析你的持股
4. 結果寫入 `docs/data.json`，由 GitHub Pages 顯示在 `docs/index.html`

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
