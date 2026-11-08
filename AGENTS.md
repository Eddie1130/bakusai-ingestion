# Repository Guidelines

## 專案結構與模組分工

- `scraping/`：HTTP 用戶端、討論串解析、店家搜尋、增量更新與爬取鎖；入口為 `python -m scraping`。
- `analysis/`：日期篩選、名字比對、提示詞產生與引用驗證；`analysis/templates/` 存放 Markdown 提示詞規格。
- `paths.py`：集中管理路徑與地區設定；需要資料位置時應呼叫此模組。
- `tests/`：離線回歸測試。
- `data/<region>/<store>/`：`bakusai.json` 與 `info.json`；`var/` 存放執行狀態。兩者皆不納入 Git。

## 安裝、測試與本機執行

使用 Python 3.13，在儲存庫根目錄執行：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

`requirements.txt` 固定執行時依賴版本。專案沒有獨立建置步驟。

- `python -m scraping`：互動式抓取或更新店家資料；確認抓取計畫後才發送請求。
- `python -m scraping refresh <店名>`：非互動式更新已抓取店家。
- `python -m analysis`：產生分析提示詞，或驗證手動貼回的模型報告。

## 程式風格與命名

採四個空白縮排；模組、函式與變數使用 `snake_case`，類別使用 `PascalCase`，常數使用 `UPPER_SNAKE_CASE`。沿用現有型別註記與模組 docstring 慣例。尚未配置統一的格式化或 lint 工具；避免順手重排無關程式碼。

操作介面與錯誤訊息使用繁體中文；抓取內容與引文保留原始日文。維持手動複製提示詞與貼回答案的流程，不自動呼叫模型。

## 測試準則

使用標準函式庫 `unittest` 與 `unittest.mock`。檔名採 `tests/test_*.py`，測試方法採 `test_*`。以暫存目錄、合成留言及 mock 隔離網路、剪貼簿與真實資料。

修改時補上對應回歸案例，優先涵蓋日期邊界、引用範圍、增量合併與穩定 ID。尚未訂定覆蓋率門檻；提交前執行完整測試。

## 提交與 Pull Request

現有提交採 `feat: 簡短描述`；後續依變更目的使用 `fix:`、`test:` 或 `docs:` 等前綴。一次提交聚焦一項變更。

PR 說明應包含問題、行為改變、執行過的驗證及相關 issue；介面輸出變更可附終端範例。推送前檢查 `git diff`，避免提交爬取資料、執行狀態或憑證。

## 資料與設定注意事項

透過 `BAKUSAI_DATA_ROOT`、`BAKUSAI_VAR_ROOT` 覆寫路徑，維持資料與執行狀態目錄分離。請求間隔不得低於 2.0 秒；`BAKUSAI_REQUEST_INTERVAL` 僅用於提高間隔。

已持久化的討論串序號與 `T8-527` 類型留言 ID 不得重新編號。引用驗證只證明留言存在，不代表論壇說法屬實；請勿公開爬取資料。
