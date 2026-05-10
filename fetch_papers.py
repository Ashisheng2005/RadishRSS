#!/usr/bin/env python3
"""Fetch and translate arXiv papers."""

import feedparser
import requests
import os
import time
import subprocess
import json
import re
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any

from log import setup_logger
from data_type import PaperResult
from mysql_function import (
    clean_html,
    mysql_is_available,
    get_mysql_connection,   
    ensure_migration_table, 
    apply_sql_migrations, 
    upsert_metadata_to_mysql
    )

# ====== Configuration ======

class Config:

    save_dir: str = os.environ.get("PAPERS_SAVE_DIR", "./papers")
    translate_output_dir: str = os.environ.get("PAPERS_OUTPUT_DIR", "./output")
    logs_dir: str = os.environ.get("PAPERS_LOGS_DIR", "./logs")

    deepseek_api_key: str = os.environ.get("DEEPSEEK_API_KEY", "")
    deepseek_base_url: str = os.environ.get(
        "DEEPSEEK_BASE_URL",
        "https://api.deepseek.com/chat/completions",
    )
    deepseek_model: str = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

    max_per_run: int = 15
    max_workers: int = 2
    download_timeout: int = 120
    api_timeout: int = 60

    max_retries: int = 3
    retry_base_delay: float = 2.0

    pdf2zh_service: str = "deepseek"

    run_interval: int = int(os.environ.get("PAPERS_RUN_INTERVAL", "0"))

    # Number of matched (符合期望方向) papers to collect per run
    target_match_count: int = int(os.environ.get("TARGET_MATCH_COUNT", "5"))

    # Optional subscriptions json file. If present, it should contain an array of
    # objects: {"feed": "https://...", "expect": "期望方向描述"}
    subscriptions_file: str = os.environ.get("SUBSCRIPTIONS_FILE", "./subscriptions.json")

    mysql_enabled: bool = os.environ.get("MYSQL_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    mysql_host: str = os.environ.get("MYSQL_HOST", "127.0.0.1")
    mysql_port: int = int(os.environ.get("MYSQL_PORT", "3306"))
    mysql_user: str = os.environ.get("MYSQL_USER", "root")
    mysql_password: str = os.environ.get("MYSQL_PASSWORD", "123456")
    mysql_database: str = os.environ.get("MYSQL_DATABASE", "radish_rss")
    mysql_charset: str = os.environ.get("MYSQL_CHARSET", "utf8mb4")
    migrations_dir: str = os.environ.get("PAPERS_MIGRATIONS_DIR", "./migrations")

    @property
    def fetched_log(self) -> str:
        return os.path.join(self.logs_dir, "fetched.log")

    @property
    def translated_log(self) -> str:
        return os.path.join(self.logs_dir, "translated.log")

    @property
    def metadata_file(self) -> str:
        return os.path.join(self.translate_output_dir, "papers_metadata.json")

    @property
    def blog_json_file(self) -> str:
        return os.path.join(self.translate_output_dir, "papers.json")

    def load_subscriptions(self) -> list[Dict[str, str]]:
        """Return list of subscriptions with keys 'feed' and 'expect'.

        Backwards-compatible: if `rss_feeds` contains strings, convert them
        to entries with empty `expect`.
        """
        subs: list[Dict[str, strl.warn]] = []
        # Try loading subscriptions_file if exists
        try:
            if os.path.exists(self.subscriptions_file):
                with open(self.subscriptions_file, encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data:
                        if isinstance(item, str):
                            subs.append({"feed": item, "expect": ""})
                        elif isinstance(item, dict):
                            subs.append({"feed": item.get("feed", ""), "expect": item.get("expect", "")})
                    if subs:
                        return subs
        except Exception:
            # Fall back to rss_feeds
            logging.Logger.warning("Failed to load subscriptions from %s, falling back to rss_feeds", self.subscriptions_file)
            exit(1)

# ====== Network Helpers ======

TRANSIENT_ERRORS = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
)


def is_transient_status(status: int) -> bool:
    return status >= 500 or status == 429


def download_pdf(url: str, filename: str, config: Config, logger: logging.Logger) -> Optional[str]:
    filepath = os.path.join(config.save_dir, filename)
    for attempt in range(config.max_retries + 1):
        try:
            resp = requests.get(url, timeout=config.download_timeout)
            if resp.status_code == 200:
                os.makedirs(config.save_dir, exist_ok=True)
                with open(filepath, "wb") as f:
                    f.write(resp.content)
                return filepath
            if is_transient_status(resp.status_code):
                raise requests.exceptions.HTTPError(f"HTTP {resp.status_code}", response=resp)
            logger.warning("Permanent HTTP %d for %s, skipping", resp.status_code, url)
            return None
        except TRANSIENT_ERRORS as e:
            if attempt < config.max_retries:
                delay = config.retry_base_delay * (2 ** attempt)
                logger.warning(
                    "Attempt %d/%d failed for %s: %s. Retry in %.1fs",
                    attempt + 1, config.max_retries, url, e, delay,
                )
                time.sleep(delay)
            else:
                logger.error("All retries exhausted for %s", url)
        except Exception as e:
            logger.error("Unexpected error downloading %s: %s", url, e)
            return None
    return None


def translate_text(text: str, config: Config, logger: logging.Logger) -> str:
    if not config.deepseek_api_key:
        logger.warning("No API key configured, returning original text")
        return text

    headers = {
        "Authorization": f"Bearer {config.deepseek_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.deepseek_model,
        "messages": [
            {
                "role": "system",
                "content": "你是一名专业的学术翻译。请将以下英文摘要翻译成中文，确保专业术语准确、语句通顺。只输出译文，不要加任何解释。",
            },
            {"role": "user", "content": text},
        ],
        "temperature": 0.7,
        "max_tokens": 8000,
        "thinking": {"type": "disabled"},
    }

    for attempt in range(config.max_retries + 1):
        try:
            resp = requests.post(
                config.deepseek_base_url,
                headers=headers,
                json=payload,
                timeout=config.api_timeout,
            )
            if resp.status_code == 200:
                result = resp.json()
                return result["choices"][0]["message"]["content"]
            if is_transient_status(resp.status_code):
                raise requests.exceptions.HTTPError(f"HTTP {resp.status_code}", response=resp)
            logger.warning("Permanent API error HTTP %d, returning original text", resp.status_code)
            return text
        except TRANSIENT_ERRORS as e:
            if attempt < config.max_retries:
                delay = config.retry_base_delay * (2 ** attempt)
                logger.warning(
                    "API attempt %d/%d failed: %s. Retry in %.1fs",
                    attempt + 1, config.max_retries, e, delay,
                )
                time.sleep(delay)
            else:
                logger.error("API retries exhausted, returning original text")
        except Exception as e:
            logger.error("Unexpected API error: %s", e)
            return text
    return text


def translate_and_filter(text: str, expect: str, config: Config, logger: logging.Logger) -> str:
    """Ask the translation API to both check expectation and translate.

    If the abstract does not match `expect`, the API should return the literal
    string "on". Otherwise it should return the translated abstract.
    """
    if not config.deepseek_api_key:
        logger.warning("No API key configured, cannot translate/filter; returning on")
        return "on"

    headers = {
        "Authorization": f"Bearer {config.deepseek_api_key}",
        "Content-Type": "application/json",
    }

    # Build a single user prompt that includes expectation and clear instructions
    user_prompt = (
        "下面给出用户对论文方向的期望描述：\n"
        f"{expect}\n\n"
        "请判断下面的英文摘要是否符合上述期望方向：如果不符合，只输出小写on，不要其他内容；"
        "否则请把摘要完整翻译成中文并且只输出译文，不要附带任何解释或额外标记。\n\n摘要：\n" + text
    )

    payload = {
        "model": config.deepseek_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是一名专业的学术翻译与分类助手。"
                    "请依据用户给出的期望方向判断摘要是否相关，然后按照上面的要求输出。"
                ),
            },
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 8000,
        "thinking": {"type": "disabled"},
    }

    for attempt in range(config.max_retries + 1):
        try:
            resp = requests.post(
                config.deepseek_base_url,
                headers=headers,
                json=payload,
                timeout=config.api_timeout,
            )
            if resp.status_code == 200:
                result = resp.json()
                content = result["choices"][0]["message"]["content"].strip()
                if content.lower() == "on":
                    return "on"
                return content
            if is_transient_status(resp.status_code):
                raise requests.exceptions.HTTPError(f"HTTP {resp.status_code}", response=resp)
            logger.warning("Permanent API error HTTP %d, returning on", resp.status_code)
            return "on"
        except TRANSIENT_ERRORS as e:
            if attempt < config.max_retries:
                delay = config.retry_base_delay * (2 ** attempt)
                logger.warning(
                    "API attempt %d/%d failed: %s. Retry in %.1fs",
                    attempt + 1, config.max_retries, e, delay,
                )
                time.sleep(delay)
            else:
                logger.error("API retries exhausted, returning on")
        except Exception as e:
            logger.error("Unexpected API error: %s", e)
            return "on"
    return "on"


# ====== pdf2zh Batch Translation ======

def batch_translate_pdfs(pdf_paths: list[str], config: Config, logger: logging.Logger) -> None:
    if not pdf_paths:
        return

    translated = set()
    if os.path.exists(config.translated_log):
        with open(config.translated_log) as f:
            translated = set(line.strip() for line in f)

    untranslated = [p for p in pdf_paths if os.path.basename(p) not in translated]
    if not untranslated:
        logger.info("All PDFs already translated, skipping.")
        return

    logger.info("Translating %d PDF(s) via pdf2zh...", len(untranslated))
    try:
        cmd = ["pdf2zh", *untranslated, "-s", config.pdf2zh_service, "-o", config.translate_output_dir]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if result.returncode == 0:
            logger.info("PDF batch translation complete")
            with open(config.translated_log, "a") as f:
                for p in untranslated:
                    f.write(os.path.basename(p) + "\n")
        else:
            logger.error("PDF translation failed: %s", result.stderr[-300:])
    except subprocess.TimeoutExpired:
        logger.error("PDF translation timed out")
    except Exception as e:
        logger.error("PDF translation error: %s", e)


# ====== State Helpers ======

def load_fetched_set(fetched_log: str) -> set[str]:
    if os.path.exists(fetched_log):
        with open(fetched_log) as f:
            return set(line.strip() for line in f if line.strip())
    return set()


def load_metadata(metadata_file: str) -> dict:
    if os.path.exists(metadata_file):
        with open(metadata_file) as f:
            return json.load(f)
    return {}


def reconcile_fetched_set(
    fetched: set[str], fetched_log: str, papers_metadata: dict, logger: logging.Logger,
) -> set[str]:
    """Merge metadata keys into fetched set and repair fetched.log if out of sync."""
    metadata_ids = set(papers_metadata.keys())
    only_in_fetched = fetched - metadata_ids
    only_in_metadata = metadata_ids - fetched

    merged = fetched | metadata_ids

    if only_in_fetched:
        logger.warning(
            "Found %d ID(s) in fetched.log but missing from metadata (orphaned)",
            len(only_in_fetched),
        )

    if only_in_metadata:
        logger.info(
            "Adding %d ID(s) from metadata to dedup set (not in fetched.log)",
            len(only_in_metadata),
        )

    # Repair fetched.log if out of sync
    if only_in_fetched or only_in_metadata:
        with open(fetched_log, "w") as f:
            for arxiv_id in sorted(merged):
                f.write(arxiv_id + "\n")
        logger.info("Repaired fetched.log: %d entries", len(merged))

    return merged


def save_metadata(metadata: dict, config: Config, logger: logging.Logger) -> None:
    os.makedirs(config.translate_output_dir, exist_ok=True)
    with open(config.metadata_file, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    logger.info("Metadata saved (%d papers)", len(metadata))

# ====== Paper Processing ======

def process_paper(entry, config: Config, logger: logging.Logger, pre_translated_zh: Optional[str] = None) -> Optional[PaperResult]:
    arxiv_id = entry.id.split("/")[-1]
    title = entry.title.strip() if hasattr(entry, "title") else "无标题"
    authors = ", ".join(a.name for a in entry.authors) if hasattr(entry, "authors") else "未知"
    summary_raw = entry.summary if hasattr(entry, "summary") else ""
    summary_clean = clean_html(summary_raw)
    pdf_url = entry.link.replace("/abs/", "/pdf/") + ".pdf"
    published = entry.published if hasattr(entry, "published") else "未知"

    filename = f"{arxiv_id}.pdf"

    logger.info("Downloading: %s", title[:80])
    pdf_path = download_pdf(pdf_url, filename, config, logger)
    if pdf_path is None:
        logger.warning("Skipped %s (download failed)", arxiv_id)
        return None

    # Use pre_translated_zh if caller already translated+filtered the abstract
    if pre_translated_zh is None:
        logger.info("Translating abstract: %s", arxiv_id)
        abstract_zh = translate_text(summary_clean, config, logger)
    else:
        abstract_zh = pre_translated_zh

    return PaperResult(
        arxiv_id=arxiv_id,
        title=title,
        authors=authors,
        abstract_en=summary_clean,
        abstract_zh=abstract_zh,
        pdf_url=pdf_url,
        pdf_local_path=pdf_path,
        published=published,
    )

# ====== Main ======

def main() -> None:
    config = Config()
    logger = setup_logger(config.logs_dir)

    os.makedirs(config.save_dir, exist_ok=True)
    os.makedirs(config.translate_output_dir, exist_ok=True)

    if config.mysql_enabled:
        logger.info("MySQL persistence enabled, applying migrations...")
        apply_sql_migrations(config, logger)

    loop = config.run_interval > 0
    if loop:
        logger.info("Loop mode enabled: checking every %d second(s)", config.run_interval)

    while True:
        fetched = load_fetched_set(config.fetched_log)
        papers_metadata = load_metadata(config.metadata_file)

        # Reconcile dedup sources: metadata keys also prevent re-download
        fetched = reconcile_fetched_set(fetched, config.fetched_log, papers_metadata, logger)

        logger.info("Starting paper fetch run...")
        start_time = time.monotonic()

        new_count = 0
        new_pdf_paths: list[str] = []

        # Track how many papers matched user expectations this run
        matched_count = 0

        # Load subscriptions (feed + expected direction)
        subs = config.load_subscriptions()

        for sub in subs:
            feed_url = sub.get("feed")
            expect = sub.get("expect", "")
            logger.info("Parsing feed: %s", feed_url)
            try:
                feed = feedparser.parse(feed_url)
            except Exception as e:
                logger.error("Feed parse failed: %s", e)
                continue

            pending = []
            for entry in feed.entries:
                arxiv_id = entry.id.split("/")[-1]
                if arxiv_id not in fetched:
                    pending.append(entry)
            if not pending:
                logger.info("No new papers in feed %s", feed_url)
                continue

            logger.info("Checking %d candidate(s) from %s", len(pending), feed_url)

            # Process entries sequentially: first ask the API whether the
            # paper matches the expectation (and obtain translation). If the
            # API returns 'on', skip; otherwise use the returned translation
            # and download the PDF / persist the paper.
            for entry in pending:
                if matched_count >= config.target_match_count:
                    logger.info("Reached target match count (%d), stopping.", config.target_match_count)
                    break

                arxiv_id = entry.id.split("/")[-1]
                summary_raw = entry.summary if hasattr(entry, "summary") else ""
                summary_clean = clean_html(summary_raw)

                logger.info("Filtering & translating abstract for %s...", arxiv_id)
                translated_or_on = translate_and_filter(summary_clean, expect, config, logger)
                if translated_or_on.strip().lower() == "on":
                    logger.info("Paper %s does not match expectation, skipping.", arxiv_id)
                    # still mark as fetched to avoid re-checking? No — we only
                    # mark as fetched when we accept and download.
                    continue

                # Accepted paper: translated_or_on contains the Chinese abstract
                result = process_paper(entry, config, logger, pre_translated_zh=translated_or_on)
                if result is not None:
                    fetched.add(result.arxiv_id)
                    with open(config.fetched_log, "a") as f:
                        f.write(result.arxiv_id + "\n")
                    papers_metadata[result.arxiv_id] = result.to_dict()
                    new_pdf_paths.append(result.pdf_local_path)
                    new_count += 1
                    matched_count += 1

            if matched_count >= config.target_match_count:
                break

        elapsed = time.monotonic() - start_time
        logger.info("Run complete. %d new papers | Duration: %.1fs", new_count, elapsed)

        save_metadata(papers_metadata, config, logger)
        # generate_blog_json(papers_metadata, config, logger)
        upsert_metadata_to_mysql(papers_metadata, config, logger)

        if new_pdf_paths:
            batch_translate_pdfs(new_pdf_paths, config, logger)
        else:
            logger.info("No new PDFs to translate.")

        if not loop:
            break
        logger.info("Sleeping for %d seconds until next run...", config.run_interval)
        time.sleep(config.run_interval)


if __name__ == "__main__":
    main()
