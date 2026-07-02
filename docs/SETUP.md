# Mailquill 設定指南

從零到跑出第一份報表,大約 10–15 分鐘。四個步驟:**安裝 → Google OAuth → 設定檔 → 第一次執行**。

---

## 0. 安裝

需要 Python 3.11+。

```bash
git clone <your-fork-or-repo-url> mailquill
cd mailquill
python3 -m venv .venv
.venv/bin/python -m pip install -e .          # 安裝套件與相依
.venv/bin/python -m pip install -e ".[dev]"   # 想跑測試再裝這個
```

安裝後會有 `mailquill` 指令(等同 `python -m mailquill.cli`)。

---

## 1. Google OAuth(讓 Mailquill 唯讀你的 Gmail)

Mailquill 只要 **Gmail 唯讀** 權限(`gmail.readonly`)。憑證只留在你本機,不上傳任何地方。

1. 進 [Google Cloud Console](https://console.cloud.google.com/) → 建立(或選)一個專案。
2. **APIs & Services → Library** → 搜尋 **Gmail API** → **Enable**。
3. **APIs & Services → OAuth consent screen**
   - User Type 選 **External**(個人帳號即可)。
   - 填必要的 App name / 支援信箱。
   - **Test users** 加入你自己的 Gmail 位址(未發布的 App 只有測試使用者能授權)。
   - Scopes 可留空,首次授權時再要求。
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   - Application type 選 **Desktop app**。
   - 建立後按 **Download JSON**,把檔案存成專案根目錄的 **`credentials.json`**。
5. 第一次執行 `mailquill run`/`bootstrap` 時會自動開瀏覽器要你授權,成功後在根目錄產生 **`token.json`**(之後免再授權)。

> `credentials.json`、`token.json` 都已在 `.gitignore`,不會進 git。

---

## 2. 設定檔

### `config.yaml`

複製範例後修改:

```bash
cp config.example.yaml config.yaml
```

最重要的是 `label`——填你在 Gmail **已經用來分類財務信件的 Label 名稱**。可以是單一名稱或清單:

```yaml
label: "財務"
# 或多個(聯集、去重):
# label:
#   - 財務
#   - 銀行帳單
#   - 信用卡
```

其餘欄位(csv/db/categories/rules/raw/passwords/credentials/token 路徑)通常用預設即可。

> 不確定 Label 的**確切名稱**?跑 `mailquill labels` 會列出你 Gmail 內所有 Label 的精確字串(含巢狀路徑),照抄進 `config.yaml` 最保險。

### `passwords.txt`

電子帳單 PDF 常有密碼(身分證、生日、卡號末四碼等)。每行一組候選密碼,`#` 開頭為註解。Mailquill 解密時會逐一嘗試:

```
# 每行一組可能的 PDF 密碼
A123456789
0801
1234
```

> `passwords.txt`、`config.yaml`、`rules.yaml` 都已在 `.gitignore`。密碼只留本機,別貼到任何對話或雲端。

---

## 3. 第一次執行

```bash
# (a) 掃既有 Label，抽出寄件網域，產生抓取規則草稿 rules.yaml
mailquill bootstrap
#     → 打開 rules.yaml 檢查/增刪，確認要抓哪些寄件者

# (b) 抓信 → 解密 PDF → 各家 parser → 正規化 → 分類 → 寫入 CSV / 重建 DB
mailquill run
#     預設只抓「今年」的信；要別的範圍見下方

# (c) 產生離線 HTML 儀表板
mailquill report
#     → report.html，直接用瀏覽器打開
```

### 日期範圍(避免一次撈全部歷史)

`run` 預設只抓當年 1/1 之後的信,避免動輒拉出好幾年的歷史。

```bash
mailquill run --since 2026-01-01   # 只抓此日期(含)之後
mailquill run --all-time           # 不限時間，掃全部歷史(慢、耗額度)
```

### 富邦(需手動下載)

富邦帳單走外部連結 + 要填身分證/生日/驗證碼,無法自動抓。自行下載 PDF 後:

```bash
mailquill ingest --bank fubon 115年02月.pdf       # 單檔
mailquill ingest --bank fubon ~/Downloads/fubon/   # 整個資料夾
```

---

## 4. 之後的日常

只有「要納入新到的信」才需要再 `run`。其餘都不必碰 Gmail:

```bash
mailquill rebuild   # 改了 categories.yaml 後，只由 CSV 重新分類、重建 DB
mailquill report    # 只由 DB 重出 HTML
```

`run` 結束會列出三份清單,幫你逐步補齊:

- **待補 parser** — 有信件但沒有對應銀行 parser(歡迎照 [CONTRIBUTING.md](../CONTRIBUTING.md) 補一支)。
- **PDF 解密失敗** — 密碼清單裡沒有可用密碼,補進 `passwords.txt` 再跑。
- **解析警告** — 金額/日期解析有疑慮的行,可回頭調 parser。

---

## 疑難排解

| 症狀 | 可能原因 / 解法 |
|---|---|
| `bootstrap` 找不到 Gmail Label | Label 名稱不符。跑 `mailquill labels` 抄精確字串;巢狀 Label 要含完整路徑(如 `財務/信用卡`)。 |
| 授權時瀏覽器顯示「未驗證的應用程式」 | OAuth consent screen 仍是測試狀態屬正常;確認你的 Gmail 已加入 **Test users**,點進階→繼續即可。 |
| `run` 卡住很久 | 抓信是最慢的一步;用 `--since` 縮小範圍。DB/報表出問題時用 `rebuild`/`report`,不必重抓。 |
| PDF 一直解不開 | 把正確密碼加進 `passwords.txt`(一行一組);該檔會列在「PDF 解密失敗」清單。 |
| 分類都變「未分類」 | 檢查 `categories.yaml` 關鍵字是否出現在商家名稱內;改完跑 `rebuild`。 |
| SQLite 壞了/刪了 | `mailquill rebuild` 由 `transactions.csv` 完整重建,資料不會遺失。 |
