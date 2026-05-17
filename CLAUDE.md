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

Four files, no framework, no tests.

### File Map

| File | Role |
|---|---|
| `fetch_papers.py` | Core: config, RSS fetching, filtering, downloading, email |
| `mysql_function.py` | MySQL persistence helpers (clean_html, migrations, upsert) |
| `email_server.py` | SMTP email sending via QQ mail |
| `log.py` | Logger setup (file + console, DEBUG level) |
| `migrations/001_init.sql` | MySQL schema: `papers` table with arXiv metadata |

### Data Flow (current)

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
    save all metadata → papers_metadata.json
    upsert to MySQL    → papers table
    batch_translate_pdfs() → pdf2zh (incremental via translated.log)
    send email         → HTML summary of new papers only
```

Key difference from earlier versions: processing is **sequential** per-entry, not parallel. The API call both filters (does it match the user's expectation?) and translates in a single request, avoiding wasted API cost on irrelevant papers.

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
| `max_workers` | `2` | (unused — kept for compat, processing is sequential) |
| `max_per_run` | `15` | Max candidates per run |
| `rss_feeds` | `[cs.AI]` | Fallback feeds when subscriptions_file is missing |
| `subscriptions_file` | `./subscriptions.json` | Primary feed source with expectations |

## Key Design Decisions

- **Filter-before-download**: `translate_and_filter()` calls the API once to both judge relevance and translate. Only if the paper matches the user's expectation (`expect` field in subscriptions.json) does the code proceed to PDF download. This avoids wasting bandwidth and API cost on irrelevant papers.
- **Sequential processing**: Entries are processed one-by-one because each requires an API call (filter+translate). No parallelism — the old ThreadPoolExecutor path is gone.
- **Subscription system**: Each feed has an `expect` field describing the user's research direction in natural language. The LLM judges relevance and only returns papers that match. `target_match_count` limits how many matching papers to collect per run.
- **Single-API-call trick**: `translate_and_filter()` sends the abstract + expectation together. If it doesn't match, the API returns `on` (a no-op signal). If it matches, the API returns the Chinese translation directly — no second call needed.
- **Dual-source dedup**: Both `fetched.log` lines and `papers_metadata.json` keys are merged at startup via `reconcile_fetched_set()`. If files drift, `fetched.log` is repaired. Orphaned IDs (in log but no metadata) are reported and auto-cleaned.
- **Incremental pdf2zh**: Only untranslated PDFs (per `translated.log`) are sent to `pdf2zh`, not the entire directory.
- **MySQL upsert**: When enabled, metadata is upserted into the `papers` table. Migration files in `migrations/` are applied in order via `schema_migrations` tracking table.
- **Email on match**: When new papers are matched, an HTML email is sent with title, authors, abstract preview, and PDF links for only the current run's papers.
- **Error tiers**: Transient (timeout, 5xx, 429) → retry with exponential backoff; Permanent (404, 403) → skip immediately; Fatal → abort.
- **Loop mode**: Set `PAPERS_RUN_INTERVAL` instead of cron. Avoids concurrent runs on the same state files.
