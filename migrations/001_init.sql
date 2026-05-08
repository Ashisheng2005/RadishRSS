CREATE TABLE IF NOT EXISTS papers (
    arxiv_id VARCHAR(64) PRIMARY KEY comment 'arXiv ID, e.g., 2101.00001',
    title TEXT NOT NULL comment 'Paper title',
    authors TEXT NOT NULL comment 'Paper authors',
    abstract_en MEDIUMTEXT comment 'English abstract',
    abstract_zh MEDIUMTEXT comment 'Chinese abstract',
    pdf_url VARCHAR(1024) comment 'PDF URL',
    published VARCHAR(128) comment 'Publication date',
    is_read TINYINT(1) NOT NULL DEFAULT 0,
    added_date DATETIME NOT NULL comment 'Date added',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP  ,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_papers_added_date (added_date),
    INDEX idx_papers_published (published)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
