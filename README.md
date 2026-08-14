# bakusai-ingestion

Scrape a store's discussion from [Bakusai](https://bakusai.com), then turn one
girl's mentions into a prompt you paste into the model of your choice — and check
every comment id the answer cites against the comments that were actually sent.

從爆サイ抓取某家店的討論串，挑出與某位女生相關的留言，組成提示詞讓你貼給模型，
再把模型引用的每一個留言 ID 拿回原始資料比對。

Two commands, files on disk, no database, no API key.

---

## Why it exists

Anonymous forum text is easy to summarise and hard to trust. A model handed a few
hundred Japanese comments will produce a fluent report and cite comment ids that
were never in the input. This tool is built around that failure:

- **Selection is closed-set string matching, not extraction.** You supply the
  name. Nothing infers who a comment is about from scratch, because that is where
  invented attributions come from.
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

### 1. Scrape a store

```bash
python -m scraping
```

Interactive. Pick a region, type a store name, and the tool searches Bakusai,
groups the threads it finds into candidate stores, and shows you the plan — how
many threads, how many pages, how long it will take — before it spends a single
request. Nothing is fetched until you agree.

Re-scrape a store already on disk, unattended:

```bash
python -m scraping refresh <店名>
```

### 2. Analyse one girl

```bash
python -m analysis
```

Interactive. Pick the region, then the store, then the mode. Two of the three
modes go on to ask for the girl's name — as written on the store's own roster —
and how far to follow reply chains (one hop by default).

| Mode | What it does | Prompts out / answers in |
|---|---|---|
| Single pass *(default)* | Builds one prompt over the selected evidence, and stops there | 1 / 0 |
| Two-step | Asks the model first for the comment ids that are really about her, checks those ids against the store, then builds the report prompt over only those, re-expanded one hop for context | 2 / 1 |
| Verify | Takes a report you already have and checks every comment id it cites | 0 / 1 |

Every prompt goes to your clipboard, and a copy is written to a temp file because
nobody selects 34,000 characters in a terminal. Where a mode reads something back,
it comes off the clipboard — press Enter — or from a file path you type. Nothing
you paste in is ever written into the project or the data tree.

**Verify** is where the fabrication rate is reported: `0.0%` means no cited id was
invented, **not** that the report is right about who it is about. **Two-step**
applies the same check one stage earlier, to the model's id list, and tells you
which ids it had to drop.

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
