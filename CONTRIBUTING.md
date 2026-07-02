# 貢獻指南 — 新增一家銀行 parser

台灣的銀行帳單版型各家不同,一個人補不完。**如果你手上有某家銀行的電子帳單、願意貢獻一支 parser,非常歡迎送 PR。** 這份文件說明架構、逐步流程,以及**最重要的去識別化規範**。

---

## 架構:為什麼加一家銀行很容易

Mailquill 的 parser 是**外掛式**的——每家銀行一支模組,只做一件事:把該行帳單的文字轉成一串統一的 `RawTxn`。它不碰 Gmail、不碰 PDF 解密、不碰分類或報表,那些都由共用管線處理。

```
Gmail 抓信 / 本地 PDF ──▶ 解密 ──▶ [你的 parser] ──▶ 正規化 ──▶ 分類 ──▶ CSV/DB/報表
                                        ↑
                            你只需要專心寫這一塊
```

`parse()` 拿到的是**已經解密、抽好文字**的內容,回傳原始字串即可(金額帶不帶逗號、日期是不是民國都沒關係)——後續 normalizer 會清洗。

### `RawTxn` 欄位

| 欄位 | 必填 | 說明 |
|---|---|---|
| `bank` | ✅ | 銀行代號(與 parser 的 `bank` 一致) |
| `date` | ✅ | 消費日,原始字串即可(如 `115/05/25`、`05/18`) |
| `amount` | ✅ | 金額,原始字串(可含逗號;負數=退款/回饋) |
| `merchant_raw` | ✅ | 商家原始描述 |
| `currency` | | 幣別,預設 `TWD` |
| `post_date` | | 入帳日 |
| `account_last4` | | 卡號末四碼 |

---

## 逐步流程

### 1. 複製範本

```bash
cp mailquill/parsers/example_bank.py mailquill/parsers/yourbank.py
```

`example_bank.py` 是最小可運作範例,可對照它的結構。

### 2. 寫 `matches()` 與 `parse()`

```python
class YourBankParser(Parser):
    bank = "YourBank"

    def matches(self, msg: EmailMessage) -> bool:
        # 用寄件網域判斷這封信是不是這家銀行的
        return "yourbank.com.tw" in msg.sender

    def parse(self, msg: EmailMessage, pdf_texts: list[str]) -> list[RawTxn]:
        # msg.body_text 一定是字串；pdf_texts 是各已解密 PDF 的逐頁文字
        blocks = [msg.body_text] + list(pdf_texts)
        txns: list[RawTxn] = []
        for block in blocks:
            for line in block.splitlines():
                # …針對版型寫正則，取出日期/商家/金額…
                txns.append(RawTxn(bank=self.bank, date=..., amount=..., merchant_raw=...))
        return txns
```

**看真實 parser 學版型技巧**:`cathay.py`(附件 PDF、民國年、跨年 rollover)、`ubot.py`(內文、剝除外幣中繼欄位、末四碼在卡別標題行)、`taishin.py`(完整民國日期)。

常見注意點:
- 略過非明細行(上期結餘、繳款、總計);通常靠「開頭是日期」來判斷。
- 卡號末四碼常在「卡別標題行」而非每一筆,記得跨行帶入 `account_last4`。
- 海外交易行常夾帶外幣金額/幣別/結匯日,要留新臺幣金額。
- 民國年:`parsers/_util.py` 有 `roc_to_ad` / `md_to_date` / `roc_date_to_iso` 可用。

### 3. 註冊

在 `mailquill/parsers/__init__.py` import 並 `register(YourBankParser())`。**放在 `ExampleBankParser` 之前**(範例只比對測試網域,順序不影響真實信件,但真實 parser 建議排前面)。

### 4. 寫測試(用合成資料)

在 `tests/test_parsers_yourbank.py` 放一段**假的**帳單文字,驗證:

- `matches()` 對正確寄件者回 `True`;
- 明細筆數、日期轉換、金額、末四碼正確;
- **明細金額總和** 對得起帳單「本期新增/總計」(這是最能證明版型解對的斷言);
- 沒有期別標頭時回空清單(防呆)。

跑測試:

```bash
.venv/bin/python -m pytest tests/test_parsers_yourbank.py
```

---

## ⚠️ 去識別化規範(送 PR 前務必做到)

**真實帳單資料——真實商家、真實金額、真實或遮罩卡號、身分證、生日——都不該進 repo,連測試 fixture、docstring 範例、commit 訊息都不行。**

請一律使用合成資料:

| 不要 | 改用 |
|---|---|
| 真實商家名(如某連鎖店) | `某商店`、`某餐廳`、`某計程車`、`某加油站`、`某海外服務` |
| 真實卡號末四碼 | `9999`、`8888` |
| 遮罩卡號 | `123456******7890` |
| 真實金額 | 隨手編、湊整的假金額 |
| 真實訂單號/保單號 | 省略或改成明顯假值 |
| 個資(身分證/生日/Email) | 完全不要出現 |

只要保留**版型結構**(欄位順序、分隔、民國年格式等)就能驗證 parser;內容是不是真的無所謂。現有的 `tests/test_parsers_*.py` 都是這樣寫的,可直接參考。

送 PR 前自我檢查一下:

```bash
git grep -nIE '真實商家關鍵字|真實卡號' -- tests mailquill   # 換成你要確認的字串
```

---

## 送 PR

1. 開一支分支,一支 parser 一個 PR 最好審。
2. 確認 `.venv/bin/python -m pytest` 全綠。
3. PR 描述附上:哪家銀行、帳單來源(Gmail 附件/內文/需手動下載)、以及你已用**合成資料**測試。

謝謝你讓 Mailquill 支援更多銀行 🙏
