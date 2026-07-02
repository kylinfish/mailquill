<!-- 謝謝貢獻！請簡述這個 PR，並勾選下方檢查項。 -->

## 這個 PR 做了什麼

## 若新增／修改銀行 parser，請確認
- [ ] `mailquill/parsers/<bank>.py`，並在 `mailquill/parsers/__init__.py` 註冊
- [ ] 測試 `tests/test_parsers_<bank>.py`，且**明細金額總和對得起帳單總計**
- [ ] **所有測試 fixture／docstring 範例皆為合成（去識別化）資料** — 無真實商家／金額／卡號／身分證／生日
- [ ] `.venv/bin/python -m pytest` 全部通過

## 帳單來源
- [ ] Gmail 附件 PDF
- [ ] Gmail 內文
- [ ] 需手動下載（如富邦）

## 備註
