# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## How to Run

```bash
# Local: one-shot fetch & translate
set DEEPSEEK_API_KEY=sk-xxx
python fetch_papers.py

# Docker compose (loop mode, checks every 12h)
docker compose up -d
docker compose logs -f arxiv-translator
```

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `DEEPSEEK_API_KEY` | — | DeepSeek API key (required for abstract translation) |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/chat/completions` | API endpoint |
| `DEEPSEEK_MODEL` | `deepseek-chat` | Model for abstract translation |
| `PAPERS_SAVE_DIR` | `./papers` | PDF storage directory |
| `PAPERS_OUTPUT_DIR` | `./output` | Metadata & blog JSON output |
| `PAPERS_LOGS_DIR` | `./logs` | Log files directory |
| `PAPERS_RUN_INTERVAL` | `0` | Loop mode (seconds); 0 = run once and exit |

**Hardcoded config** (change in `Config` dataclass if needed):

| Field | Default | Purpose |
|---|---|---|
| `rss_feeds` | `[cs.AI]` | arXiv RSS feed URLs |
| `max_per_run` | `15` | Max new papers per run |
| `max_workers` | `5` | Thread pool size |
| `pdf2zh_service` | `deepseek` | Translation backend for PDF batch |

## Architecture

Single-file script (`fetch_papers.py`) — no framework, no package, no tests.

### Sections

- **Config** — `Config` dataclass, env var overrides, derived path properties
- **Paper Processing** — `process_paper()` per-paper: RSS entry → download PDF → translate abstract
- **Network Helpers** — `download_pdf()` / `translate_text()` with exponential backoff, transient vs permanent error distinction
- **State** — Three persistence files reconciled at startup via `reconcile_fetched_set()`
- **Main Loop** — `while True` in `main()` if `run_interval > 0`, else single pass

### Data Flow

```
arXiv RSS → feedparser → dedup (fetched.log + metadata.json keys)
                           ↓
                    ThreadPoolExecutor (max_workers=5)
                     ├── download_pdf()  → {save_dir}/{arxiv_id}.pdf
                     └── translate_text() → abstract_zh (DeepSeek API)
                           ↓
                    save_metadata()      → papers_metadata.json
                    generate_blog_json() → papers.json
                    batch_translate_pdfs() → pdf2zh (incremental via translated.log)
```

### State Files

| File | Role | Format |
|---|---|---|
| `{logs_dir}/fetched.log` | Download dedup | One arXiv ID per line |
| `{logs_dir}/translated.log` | PDF translation dedup | One filename per line |
| `{logs_dir}/fetch_papers.log` | Run log (DEBUG level) | Timestamped lines |
| `{output_dir}/papers_metadata.json` | Canonical store | `{arxiv_id: {title, authors, abstract_en, abstract_zh, ...}}` |
| `{output_dir}/papers.json` | Frontend-consumable list | Sorted array; user `is_read` edits preserved across runs |

## Key Design Decisions

- **Download before translate**: PDF must succeed before calling translation API, avoiding wasted API cost.
- **Dual-source dedup**: Both `fetched.log` lines and `papers_metadata.json` keys are merged at startup. If one file drifts, `reconcile_fetched_set()` writes a repaired `fetched.log`.
- **papers.json merge**: `is_read` from the existing `papers.json` is preserved on regeneration; all other fields come from `papers_metadata.json`.
- **Incremental pdf2zh**: Only untranslated PDFs (per `translated.log`) are sent to pdf2zh, not the entire directory.
- **Loop mode replaces cron**: Set `PAPERS_RUN_INTERVAL` instead of using an external cron job. Avoids concurrent runs on the same state files.
- **Error tiers**: Transient (timeout, 5xx, 429) → retry with backoff up to `max_retries`; Permanent (404, 403) → skip immediately; Fatal → abort.
- **Orphaned IDs**: IDs in `fetched.log` without metadata entries are reported as "orphaned" each run and automatically removed on the next `fetched.log` repair.
