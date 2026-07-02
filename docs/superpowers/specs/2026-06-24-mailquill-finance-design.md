# mailquill — 本地 Gmail 財務歸檔與報表

**日期**：2026-06-24
**狀態**：設計已確認，待寫實作計畫

## 目標

做一個在本機跑的 agent，讀取使用者 Gmail 中的財務相關信件（台灣銀行/信用卡的帳單、消費通知、收據），收攏並歸檔，產生報表供使用者核對：

- 消費紀錄
- 金流紀錄
- 消費類別統計

硬需求：

- **全本地處理**：資料不出本機；解析為純 rule-based，零外部 LLM/雲端呼叫。
- **可解密 PDF**：台灣銀行帳單常為加密 PDF，需用本地密碼清單解開。

## 技術棧

- 語言：Python
- Gmail：`google-api-python-client`（OAuth，read-only scope）
- PDF 解密：`pikepdf`（封裝 qpdf）
- PDF 文字抽取：`pdfplumber`
- 報表：`jinja2` 產生自包含 HTML（圖表用內嵌 JS）
- 儲存：CSV（真實來源）+ `sqlite3`（可重建查詢層）
- CLI：`click` 或 `argparse`

## 範圍邊界

- **觸發方式**：僅手動指令，不做自動化/排程。
- **解析方式**：純 rule-based（正則 + 各家版型模板），不使用 LLM（本地或雲端皆不用）。
- **來源**：台灣的銀行/信用卡；信件型態約一半 PDF 附件、一半 email 內文/HTML。

## 架構與資料流

```
                       ┌─ 使用者已分類的 Gmail Label
                       │
  [bootstrap]  掃 Label → 抽出寄件者/關鍵字 → 產生 rules.yaml(草稿) → 使用者確認
                       │
  [run] ─────────────────────────────────────────────────────────────────
   Gmail API 抓信(依 rules) → 內文 & PDF 附件
        │                          │
        │                    解密 PDF(pikepdf + 密碼清單)
        │                          │
        └──────► rule-based 解析器(各家版型模板) ──► 正規化交易
                                                        │
                                          套用 categories.yaml 兩層分類
                                                        │
                          ┌─────────────────────────────┤
                          ▼                             ▼
                  transactions.csv              rebuild ► SQLite
                (真實來源, 去重累積)                     │
                                                  HTML dashboard(查詢/圖表)
```

**核心原則**：

- `transactions.csv` 是唯一真實來源，耐久、可長期累積。
- SQLite 與所有報表皆由 CSV 可重建（`mailquill rebuild`）。
- 萬一 SQLite 損壞/遺失，從 CSV 一鍵重建。
- 修改解析規則或分類規則後重跑 = 全部重新處理，**不需重抓信**（CSV 已留存原始參照）。

## 信件識別（rules）

1. `bootstrap` 掃描使用者已分類的 Gmail Label，抽出目前的寄件者網域/地址與主旨關鍵字。
2. 產生 `rules.yaml` 草稿，使用者確認後才正式使用。
3. 正式 `run` 時用 rule-based 比對（寄件者為主、關鍵字為輔），**不單靠 Label**，避免 Label 偶爾漏接。

## PDF 解密

- 使用者提供本地密碼清單（設定檔，標註不進 git）。
- `pdf_unlocker` 逐一嘗試清單中的密碼解開加密 PDF。
- 全部失敗 → 記錄 `unlock_failures.log`，原檔留存，不中斷整批。

## 分類（兩層、A+B、可動態調整）

- **第二層（A）**：商家關鍵字對照表，`關鍵字 → (第一層, 第二層)`。
- **第一層輔助（B）**：若帳單自帶 MCC/類別欄位，優先用來輔助判定第一層。
- 規則存於可編輯的 `categories.yaml`；先提供預設起始版，使用者隨時手動微調。
- 第一層：食 / 衣 / 住 / 行 / 育 / 樂 / 醫療 / 其他（可調整）。
- 第二層：常見渠道/品類（可調整）。
- 比對不到 → `category_l1=未分類`，列入「待補分類」清單；編規則後 `rebuild` 重新分類。

## 元件

| 元件 | 職責 | 主要依賴 |
|---|---|---|
| `gmail_client` | OAuth 登入、依 rules 抓信、取內文與附件 | `google-api-python-client` |
| `pdf_unlocker` | 用密碼清單逐一試解加密 PDF，回傳明文 PDF | `pikepdf` |
| `bootstrap` | 掃既有 Label，抽寄件者/關鍵字 → 產生 `rules.yaml` 草稿 | — |
| `parsers/` | 各家銀行一個解析模組（內文 + PDF 版型），輸出原始欄位 | `pdfplumber`、`re` |
| `normalizer` | 把各家原始欄位映射成統一 schema、清洗金額/日期 | — |
| `categorizer` | 套 `categories.yaml`，產生第一層/第二層分類 | — |
| `store` | 寫入/去重 `transactions.csv`；由 CSV 重建 SQLite | `sqlite3`、`csv` |
| `report` | 由 SQLite 產生自包含 HTML dashboard | `jinja2` + 內嵌 JS 圖表 |
| `cli` | `bootstrap` / `run` / `rebuild` / `report` 指令 | `click` 或 `argparse` |

新增一家銀行 = 在 `parsers/` 加一個模組 + 一份樣本測試，其他元件不動（靠統一 schema 隔離）。

## 正規化交易 schema（CSV 欄位）

```
txn_id          # 去重用雜湊：source_account+date+amount+merchant_raw
date            # 交易日 YYYY-MM-DD
post_date       # 入帳日(若有)
amount          # 正=支出, 負=退款/收入(或另設 direction 欄)
currency        # TWD 等
merchant_raw    # 原始商家字串
merchant_norm   # 正規化後商家
category_l1     # 第一層: 食/衣/住/行/育/樂/醫療/其他
category_l2     # 第二層: 渠道/品類
bank            # 來源銀行
account_last4   # 卡號/帳號末4碼(若有)
source_type     # email_body | pdf
source_msg_id   # Gmail message id(可回溯原信)
raw_ref         # 原始 PDF/內文存檔路徑
imported_at
```

- 去重靠 `txn_id`，重跑同一封信不重複入帳。
- 每筆可用 `source_msg_id` 回溯原始 Gmail，便於核對。

## 報表

- 由 SQLite 產生自包含 HTML dashboard，含：
  - 消費分類圓餅圖（第一層，可下鑽第二層）
  - 月度長條圖
  - 金流時間軸
  - 明細查詢表（可回溯原信）
- 另可輸出正規化 CSV 明細供使用者自行拉樞紐核對。

## 錯誤處理（永不靜默丟資料）

- **PDF 解不開**：記 `unlock_failures.log`，原檔留存，不中斷。
- **無對應 parser / 版型不符**：標 `needs_parser`，原始內容存檔，列入 run 結束報告。
- **解析出但分類不到**：`category_l1=未分類`，列入「待補分類」清單。
- **金額/日期清洗失敗**：標 `parse_warning`，保留原始字串。

## 測試策略

- 每個 parser 配去識別化樣本（內文 HTML / PDF 文字片段）做單元測試。
- `normalizer`、`categorizer`、`store`(去重) 以合成資料做純函式測試。
- `pdf_unlocker` 用自製加密測試 PDF。
- 走 TDD：先寫 parser 期望輸出測試，再實作。

## CLI 指令

- `mailquill bootstrap` — 掃 Label 產生 `rules.yaml` 草稿供確認
- `mailquill run` — 依 rules 抓新信、解密、解析、正規化、分類、更新 CSV + SQLite
- `mailquill rebuild` — 由 CSV 重建 SQLite（含重新分類）
- `mailquill report` — 由 SQLite 產生 HTML dashboard

## 重用的開源元件（參考）

- 解析參考：`sebastienrousseau/bankstatementparser`（本地優先帳單解析，可選擇性重用）
- Gmail 收信模式參考：`rakshran/expense-tracker`
- PDF 解密：`pikepdf` / `qpdf`

## YAGNI（明確排除）

- 不做排程/自動化（僅手動）
- 不用任何 LLM（本地或雲端）
- 不接外部財務 SaaS（如 YNAB）
- 不做多使用者 / 雲端部署
