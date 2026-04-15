import hashlib
import re
import unicodedata


def normalize_query(query: str) -> str:
    text = unicodedata.normalize("NFKD", query)
    # Remove combining marks (accents, diaereses, etc.) so ü→u, é→e
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower().strip()
    # Collapse apostrophes so "don't" → "dont", not "don t"
    text = re.sub(r"[''\u2019]", "", text)
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def query_hash(query: str) -> str:
    return hashlib.sha256(normalize_query(query).encode()).hexdigest()[:16]
