# ====== Utility ======
import re
import logging
import os

from datetime import datetime

try:
    import pymysql
except ImportError:
    pymysql = None


def clean_html(raw_html: str) -> str:
    return re.sub(r"\s+", " ", re.sub("<.*?>", "", raw_html)).strip()


def mysql_is_available(config, logger: logging.Logger) -> bool:
    if not config.mysql_enabled:
        return False
    if pymysql is None:
        logger.error("MYSQL_ENABLED is true but pymysql is not installed")
        return False
    return True


def get_mysql_connection(config):
    return pymysql.connect(
        host=config.mysql_host,
        port=config.mysql_port,
        user=config.mysql_user,
        password=config.mysql_password,
        database=config.mysql_database,
        charset=config.mysql_charset,
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )
    

def ensure_migration_table(conn) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version VARCHAR(255) PRIMARY KEY,
                applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """,
        )
    conn.commit()


def apply_sql_migrations(config, logger: logging.Logger) -> None:
    if not mysql_is_available(config, logger):
        return

    if not os.path.isdir(config.migrations_dir):
        logger.warning("Migrations dir not found: %s", config.migrations_dir)
        return

    migration_files = sorted(
        f for f in os.listdir(config.migrations_dir)
        if f.endswith(".sql")
    )

    if not migration_files:
        logger.info("No SQL migration files found in %s", config.migrations_dir)
        return

    conn = get_mysql_connection(config)
    try:
        ensure_migration_table(conn)
        with conn.cursor() as cursor:
            cursor.execute("SELECT version FROM schema_migrations")
            applied = {row["version"] for row in cursor.fetchall()}

        pending = [f for f in migration_files if f not in applied]
        if not pending:
            logger.info("MySQL migrations already up to date")
            return

        for filename in pending:
            sql_path = os.path.join(config.migrations_dir, filename)
            with open(sql_path, encoding="utf-8") as f:
                sql_content = f.read().strip()

            if not sql_content:
                logger.warning("Skip empty migration: %s", filename)
                continue

            logger.info("Applying migration: %s", filename)
            with conn.cursor() as cursor:
                for statement in sql_content.split(";"):
                    statement = statement.strip()
                    if statement:
                        cursor.execute(statement)
                cursor.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (filename,),
                )
            conn.commit()

        logger.info("Applied %d migration(s)", len(pending))
    except Exception as e:
        conn.rollback()
        logger.error("Failed to apply MySQL migrations: %s", e)
        raise
    finally:
        conn.close()


def upsert_metadata_to_mysql(metadata: dict, config, logger: logging.Logger) -> None:
    if not mysql_is_available(config, logger):
        return
    if not metadata:
        return

    rows = []
    for arxiv_id, paper in metadata.items():
        rows.append((
            arxiv_id,
            paper.get("title", ""),
            paper.get("authors", ""),
            paper.get("abstract_en", ""),
            paper.get("abstract_zh", ""),
            paper.get("pdf_url", ""),
            paper.get("published", ""),
            1 if paper.get("is_read", False) else 0,
            paper.get("added_date", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ))

    conn = get_mysql_connection(config)
    try:
        with conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO papers (
                    arxiv_id,
                    title,
                    authors,
                    abstract_en,
                    abstract_zh,
                    pdf_url,
                    published,
                    is_read,
                    added_date
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    title = VALUES(title),
                    authors = VALUES(authors),
                    abstract_en = VALUES(abstract_en),
                    abstract_zh = VALUES(abstract_zh),
                    pdf_url = VALUES(pdf_url),
                    published = VALUES(published),
                    updated_at = CURRENT_TIMESTAMP
                """,
                rows,
            )
        conn.commit()
        logger.info("MySQL upsert complete (%d papers)", len(rows))
    except Exception as e:
        conn.rollback()
        logger.error("Failed to persist metadata to MySQL: %s", e)
        raise
    finally:
        conn.close()