# bakusai-ingestion

Scrape a store's discussion from [Bakusai](https://bakusai.com), then analyse a
named girl, search by features, or discover the store's roster using a prompt
you paste into the model of your choice — and check
every comment id the answer cites against the comments that were actually sent.

從爆サイ抓取某家店的討論串，依名字、外貌特徵或全店女生整理，組成提示詞讓你貼給模型，
再把模型引用的每一個留言 ID 拿回原始資料比對。

Two commands, files on disk, no database, no API key.

---

## Why it exists

Anonymous forum text is easy to summarise and hard to trust. A model handed a few
hundred Japanese comments will produce a fluent report and cite comment ids that
were never in the input. This tool is built around that failure:

- **Named selection uses closed-set string matching.** Feature search and roster
  discovery instead give the model all comments within the requested period and
  ask for evidence-backed candidates, including unnamed people. These are forum
  inferences, with uncertainty and possible duplicates, rather than a verified roster.
- **Every citation is checked.** The answer you paste back is intersected with the
  ids that were actually in the prompt, and the fabrication rate is reported. A
  rate of 0.0% means no id was invented — not that the report is right.
- **Attribution confidence is computed, not asked for.** A model returning `0.83`
  is a number with no calibration behind it. Levels here come from rules over
  facts the selection already recorded, so any level can be explained.
- **The model is never called for you.** You copy the prompt out and paste the
  answer in, so a human is always in the loop by construction.

---

## Install

Python 3.13.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

`sudachipy` and `sudachidict-core` back a grammar filter that drops matches where a
short kana name is provably ordinary Japanese rather than a name. Without them
nothing breaks; the selection is merely wider.

---

## Use

### 圖形工作台（建議）

完成上方安裝後，在儲存庫根目錄執行：

```bash
python -m gui
```

會自動開啟瀏覽器，顯示僅本機可存取的工作台。macOS 也可以雙擊
`dashboard.command`；使用期間請保留終端機視窗，按 `Ctrl+C` 結束。
GUI 使用 Python 標準函式庫與本機 HTML/CSS/JavaScript，不需額外前端套件或建置步驟。

- **我的店家**：依地區或名稱篩選已抓取店家，查看留言數、日期範圍與更新狀態。
- **新增／更新店家**：搜尋後選擇候選店家，確認抓取計畫，再於背景抓取。
  關閉進度視窗仍會繼續，可從右上角重新開啟；停止後保留已完成的討論串。
  等待確認超過 30 分鐘會取消。維持共用抓取鎖與至少 2 秒的請求間隔。
- **分析提示詞**：支援已知名字、特徵尋人、全店女生整理、其他／自訂分析，以及名字的兩段式分析。
  提供日期快捷選項、全文預覽、複製與下載，顯示字元數與 tokens 粗估。
- **其他／自訂分析**：直接填寫給模型的要求，自行挑選證據。先依留言日期、討論串多選、
  ID／編號範圍篩選，再依最新或最舊順序取前 N 則（預設 200，0 表示全部；日期不明排在最後）。
  編號可填 `T1-100～T1-200, T2-50`；只選一串時可簡寫 `100～200`。
  按「預覽並勾選留言」後可跨頁逐則勾選、全選或取消，畫面會顯示符合、本批與已選數量。
  預覽會固定資料快照，產生提示詞只納入最後勾選的留言，不自動補入其他留言或展開回覆。
  修改篩選條件後須重新預覽；分析要求的修改不會清除已勾選留言。
- **提示詞設定**：從右上角「提示詞設定」或分析頁的「調整範本」開啟編輯器。
  可分別修改已知名字報告、第一輪留言篩選、特徵尋人、全店女生整理、自訂分析的起始範本，以及共用的
  評估面向與證據規則。點選變數按鈕可插入 `$store`、`$girl` 或 `$dimensions`；
  日期範圍、留言及引用 ID 仍由程式附上。每份最多 50,000 字元，支援還原預設與取消修改。
  「儲存設定」會一次保留各範本的修改，套用到之後新產生的提示詞；既有提示詞與兩段式
  第二輪沿用分析開始時的設定。設定保存在目前瀏覽器的網站儲存空間，重新開啟相同網址
  （含連接埠）仍可使用；清除網站資料後會恢復預設。CLI 繼續使用專案內建範本。
- **報告與引用**：貼回模型報告，點選引用查看日文原文。
  預設核對當次提示詞實際送出的留言；也可改為目前店家與所選期間。
  分別標示範圍內、期間外（含日期不明）、未送出與不存在，沒有引用時顯示無法驗證。
- **留言瀏覽**：搜尋原文、討論串名稱與引用 ID，依日期由新到舊分頁閱讀。

提示詞的驗證快照只保存在程式記憶體中，最多保留最近 8 份；服務重啟或快照被淘汰後，
需重新產生提示詞。貼回的模型報告不會寫入資料目錄，重新整理頁面前請自行保留。
引用存在不代表說法屬實，仍須閱讀原文判斷。

可使用 `python -m gui --port 8766` 更換連接埠，或加上 `--no-browser` 手動開啟網址。
工作台固定綁定 `127.0.0.1`，不提供遠端存取；原有 CLI 可繼續使用。

### 1. Scrape a store

```bash
python -m scraping
```

Interactive. Pick a region, type a store name, and the tool searches Bakusai,
groups the threads it finds into candidate stores, and shows you the plan — how
many threads, how many pages, how long it will take — before it spends a single
request. Nothing is fetched until you agree.

啟動後可選 `1=抓取店家`（預設）或 `2=更新已抓店家`。
更新模式會列出本機已抓取的店家（含地區），選擇編號即可搜尋新增討論串與最新留言，
並在抓取前顯示計畫；按 Enter 開始、`v` 檢視明細、`q` 取消。
更新採最近 24 個月的抓取範圍，保留原有歷史資料與留言 ID，合併新資料並避免重複。
沒有新留言的討論串會跳過；若找不到符合所選店家身分的搜尋結果，會回報失敗，
不會自動改抓其他同名店。

Re-scrape a store already on disk, unattended:

```bash
python -m scraping refresh <店名>
```

### 2. Choose an analysis target

```bash
python -m analysis
```

選擇地區與店家後，可選：

| 分析目標 | 行為 |
|---|---|
| 1. 已知名字（預設） | 輸入源氏名，保留原本單次／兩段式分析 |
| 2. 特徵尋人 | 輸入外貌、服裝、紋身等描述；模型從期間內全部留言整理可能人選、符合與矛盾之處 |
| 3. 全店女生整理 | 模型整理具名及未具名人物、特徵、別名、在籍說法與留言證據；不確定是否同一人時保留疑問 |
| 4. 驗證報告 | 核對任何模式產生的報告引用 |

所有目標都會詢問開始與結束日期，**預設兩欄留空＝全部已爬取資料**。
支援 `YYYY`、`YYYY-MM`、`YYYY-MM-DD`、`now`，以留言發表日期（店家當地時間）
判斷，包含首尾日。例如：

- `2025-08-30` 到 `now`：2025 年 8 月 30 日至今天。
- `2023` 到 `2025`：2023 年 1 月 1 日至 2025 年 12 月 31 日。
- 只填開始或結束：另一端不設限制。

只要指定任一日期界線，日期不明的留言（包含無日期開串文）就會排除，
回覆鏈也不能帶回區間外留言。驗證模式會把區間外或日期不明 ID 視為不在驗證範圍，
不把它們直接宣稱為捏造。全部時間模式則保留無日期留言。

特徵尋人與全店整理不會先做名字或字面關鍵字篩選，讓模型能比對中文描述與日文留言。
這兩種模式各產生一份提示詞，顯示總字元數；資料量大時須確認模型能容納全文。
結果只代表留言可支持的人物線索，不能保證找齊全店人員，也不能確認目前仍在職。
沿用手動複製提示詞給 LLM 的流程，不會自動呼叫 API。

已知名字的工作流程：

| Mode | What it does | Prompts out / answers in |
|---|---|---|
| Single pass *(default)* | Builds one prompt over the selected evidence, and stops there | 1 / 0 |
| Two-step | Asks the model first for the comment ids that are really about her, checks those ids against the comments actually sent, then builds the report prompt over only those, re-expanded one hop for context | 2 / 1 |
| Verify | Takes a report you already have and checks every comment id it cites | 0 / 1 |

Every prompt goes to your clipboard, and a copy is written to a temp file because
nobody selects 34,000 characters in a terminal. Where a mode reads something back,
it comes off the clipboard — press Enter — or from a file path you type. Nothing
you paste in is ever written into the project or the data tree.

每次產生提示詞時，都會顯示完整文字的字元數與**預估輸入 tokens 範圍**，
包含分析指令、留言、日期及引用 ID；兩段式的每一輪會分別計算。
未指定目標模型，因此使用 UTF-8 位元組數 ÷ 4 至 ÷ 2 作離線粗估，
不是模型 tokenizer 的精確計數，也不是保證上下限，實際用量可能超出此範圍。
中日文會依 UTF-8 位元組長度處理，不會直接將所有字元數除以四。
估算不含你在模型端的既有對話、系統指令及模型輸出，不能用來保證符合上下文容量或帳單用量。

**Verify** is where the fabrication rate is reported: `0.0%` means no cited id was
invented, **not** that the report is right about who it is about. **Two-step**
applies the same check one stage earlier, to the model's id list, and tells you
which ids it had to drop. IDs that exist in the store but were not in the first
prompt are also dropped.

---

## Where things live

```text
data/<region>/<store>/bakusai.json   the comments, the truth for everything else
data/<region>/<store>/info.json      what was scraped, when, from which boards
var/scrape.lock                      one scrape at a time
```

Override with `BAKUSAI_DATA_ROOT` and `BAKUSAI_VAR_ROOT`. The runtime tree sits
*beside* the data tree, never inside it, so "delete the runtime state" is never one
mistyped path away from deleting a scrape.

A comment id is `T8-527` — thread 8, comment 527 — and it is the citation
primitive the whole tool is built on. Thread sequence numbers are assigned once
and persisted, never recomputed, because renumbering a thread would silently
redirect every reference to it.

---

## Rate limiting — please read

**The floor is 2.0 seconds between requests and the code will not let you go
below it.** `BAKUSAI_REQUEST_INTERVAL` can only raise it.

This is not a guess. At 1.6s a run settles into roughly ten pages, a block, a 45
second recovery, ten pages again — slower overall than simply waiting. At 2.0s a
measured 20-page walk never triggered a block at all.

Two failure modes worth knowing about, because neither announces itself:

- **A block arrives as HTTP 200** with a collapsed body. Status codes carry no
  signal here; the client detects blocks by content and backs off multiplicatively.
- **Incomplete browser headers silently break pagination.** Send only
  `User-Agent` and `Referer` and the server ignores your page parameter and pins
  you to a fixed window — 200 status, full-size page, comments that parse
  perfectly, and completely wrong data. The `Sec-Fetch-*` and client-hint group is
  what makes pagination work, which is why the client sends a full browser header
  set.

That second one is the reason this tool exists in the shape it does: the dangerous
scraping bug is not the one that crashes.

---

## Scope, and what this is not

It reads one site. It does not rank stores, score girls, recommend anything, or
publish anywhere. The model is asked for assessments and the evidence behind them,
never for a number to sort by.

All operator-facing output is Traditional Chinese. Scraped data, comments and
quotations stay in the original Japanese and are never translated.

---

## Please use this responsibly

Bakusai's content is written by real people about real people, including named
workers at named businesses. Before you point this at anything:

- Respect Bakusai's terms of service and `robots.txt`. Complying with them is your
  responsibility, not this tool's.
- Keep the 2.0s floor. It exists so that one user's scrape is not another person's
  outage.
- Do not republish what you collect. Scraped data is deliberately gitignored here
  and should stay off the public internet.
- Treat forum claims about identifiable people as forum claims. A verified
  citation means the comment exists, not that it is true.

Provided for research and personal use, as is, without warranty. See `LICENSE`.

---

## License

MIT. See `LICENSE`.
