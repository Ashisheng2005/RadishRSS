# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## How to Run

```bash
set DEEPSEEK_API_KEY=sk-xxx
python fetch_papers.py          # one-shot: fetch, filter, download, email
PAPERS_RUN_INTERVAL=43200 python fetch_papers.py   # loop mode (every 12h)
```

Requires Python 3.10+ and [pdf2zh](https://github.com/Byaidu/PDFMathTranslate) (for PDF batch translation):
```bash
pip install feedparser requests pymysql pdf2zh
```

## Environment Variables

### Required
| Variable | Purpose |
|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek API key for abstract translation & filtering |

### Optional
| Variable | Default | Purpose |
|---|---|---|
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/chat/completions` | API endpoint |
| `DEEPSEEK_MODEL` | `deepseek-chat` | Model for abstract translation/filtering |
| `PAPERS_SAVE_DIR` | `./papers` | PDF storage directory |
| `PAPERS_OUTPUT_DIR` | `./output` | Metadata JSON output directory |
| `PAPERS_LOGS_DIR` | `./logs` | Log files directory |
| `PAPERS_RUN_INTERVAL` | `0` | Loop mode (seconds); 0 = run once and exit |
| `TARGET_MATCH_COUNT` | `1` | Number of matched papers to collect per run |
| `SUBSCRIPTIONS_FILE` | `./subscriptions.json` | JSON file with feeds + expectation descriptions |

**MySQL** (default disabled, set `MYSQL_ENABLED=1` to enable):
| Variable | Default | Purpose |
|---|---|---|
| `MYSQL_HOST` | `127.0.0.1` | MySQL host |
| `MYSQL_PORT` | `3306` | MySQL port |
| `MYSQL_USER` | `root` | MySQL user |
| `MYSQL_PASSWORD` | `123456` | MySQL password |
| `MYSQL_DATABASE` | `radish_rss` | MySQL database |
| `MYSQL_CHARSET` | `utf8mb4` | Connection charset |
| `PAPERS_MIGRATIONS_DIR` | `./migrations` | SQL migration files directory |

**Email notification** (sent when new papers are matched):
| Variable | Default | Purpose |
|---|---|---|
| `EMAIL_HOST` | `smtp.qq.com` | SMTP server |
| `EMAIL_PORT` | `465` | SMTP port (SSL) |
| `EMAIL_PWD` | *(hardcoded)* | SMTP password |
| `EMAIL_SENDER` | `radishtools@foxmail.com` | From address |
| `EMAIL_RECEIVERS` | `repork@qq.com` | Comma-separated recipients |

## Code Architecture

Five Python source files, no framework, no tests.

### File Map

| File | Role |
|---|---|
| `fetch_papers.py` | Core: config, RSS fetching, filtering, downloading, email |
| `mysql_function.py` | MySQL persistence helpers (clean_html, migrations, upsert) |
| `email_server.py` | SMTP email sending via QQ mail |
| `log.py` | Logger setup (file + console, DEBUG level) |
| `data_type.py` | Unused — `PaperResult` dataclass is defined inline in `fetch_papers.py` |
| `migrations/001_init.sql` | MySQL schema: `papers` table with arXiv metadata |

### Data Flow

```
subscriptions.json ──→ load_subscriptions()
                            ↓
for each subscription (feed + expect):
    feedparser.parse(feed_url) → pending entries (dedup via fetched.log + metadata.json)
                                  ↓
    for each pending entry (sequentially):
        translate_and_filter(abstract_en, expect) ──→ DeepSeek API
              ├── "#<NO>#" → skip (doesn't match expectation)
              └── translation → process_paper()
                                  ├── download_pdf() → {save_dir}/{arxiv_id}.pdf
                                  └── PaperResult (title, authors, abstract_zh, ...)
                                      ↓
    save all metadata → papers_metadata.json (atomic write)
    upsert to MySQL    → papers table (non-fatal on error)
    batch_translate_pdfs() → pdf2zh (incremental via translated.log)
    send email         → HTML summary of new papers only
```

### State Files

| File | Role | Format |
|---|---|---|
| `{logs_dir}/fetched.log` | Download dedup | One arXiv ID per line |
| `{logs_dir}/translated.log` | PDF translation dedup | One filename per line |
| `{logs_dir}/fetch_papers.log` | Run log (DEBUG level) | Timestamped lines |
| `{output_dir}/papers_metadata.json` | Canonical store | `{arxiv_id: {title, authors, abstract_en, abstract_zh, ...}}` |

### Config

`Config` dataclass with env var overrides. Notable fields:

| Field | Default | Purpose |
|---|---|---|
| `target_match_count` | `1` | Stop after this many matched papers |
| `run_interval` | `0` | Loop mode interval (seconds); 0 = one-shot |
| `rss_feeds` | `[cs.AI]` | Fallback feeds when subscriptions_file missing |
| `subscriptions_file` | `./subscriptions.json` | Primary feed source with expectations |

## Key Design Decisions

- **Filter-before-download**: `translate_and_filter()` calls the API once to both judge relevance and translate. Only if the paper matches the user's expectation (`expect` field in subscriptions.json) does the code proceed to PDF download. This avoids wasting bandwidth and API cost on irrelevant papers.
- **Single-API-call trick**: The user prompt embeds the expectation description and tells the API to return `#<NO>#` for non-matching papers, or the Chinese translation for matching ones. No second API call needed.
- **Sequential processing**: Entries are processed one-by-one because each requires a synchronous API call. The old ThreadPoolExecutor path is gone.
- **Subscription system**: Each feed has an `expect` field describing the user's research direction in natural language. `target_match_count` limits how many matching papers to collect per run.
- **Dual-source dedup**: Both `fetched.log` lines and `papers_metadata.json` keys are merged at startup via `reconcile_fetched_set()`. If files drift, `fetched.log` is repaired. Orphaned IDs (in log but no metadata) are reported and auto-cleaned.
- **Empty feed detection**: If `feedparser.parse()` returns 0 entries, a WARNING is logged (not just "No new papers"), so transient network issues in Docker are visible.
- **Atomic metadata writes**: `papers_metadata.json` is written via tmp file + `os.replace()` to prevent corruption. Empty metadata dicts are rejected.
- **Graceful MySQL degradation**: MySQL connection/migration/upsert failures are caught and logged, never crash the main loop. Metadata always goes to JSON file regardless of MySQL state.
- **Incremental pdf2zh**: Only untranslated PDFs (per `translated.log`) are sent to `pdf2zh`, not the entire directory.
- **Error tiers**: Transient (timeout, 5xx, 429) → retry with exponential backoff; Permanent (404, 403) → skip immediately; Fatal → abort.
- **Loop mode**: Set `PAPERS_RUN_INTERVAL` instead of cron. Avoids concurrent runs on the same state files.

## Common Issues

### fetched.log accumulation blocks new papers
`fetched.log` retains every arXiv ID ever matched. If `papers_metadata.json` is lost/reset (Docker volume reset, manual deletion, etc.), the metadata orphans accumulate in `fetched.log` forever. When the orphan count exceeds the feed size, all feed entries appear as "already fetched" → zero papers processed.

**Fix:** Delete `logs/fetched.log` to reset the dedup state. The script will reprocess all current feed entries.

### translate_and_filter return value mismatch
The function's system prompt says to return `#<NO>#` for non-matching papers. The caller and the function itself must agree on this sentinel. If they fall out of sync (e.g. old `on` format), non-matching papers can accidentally pass through as matches, or matching papers get skipped.
