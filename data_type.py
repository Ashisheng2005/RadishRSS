# ====== Data Types ======
import datetime

class PaperResult:
    arxiv_id: str
    title: str
    authors: str
    abstract_en: str
    abstract_zh: str
    pdf_url: str
    pdf_local_path: str
    published: str

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "authors": self.authors,
            "abstract_en": self.abstract_en,
            "abstract_zh": self.abstract_zh,
            "pdf_url": self.pdf_url,
            "published": self.published,
            "is_read": False,
            "added_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }