import re
from typing import Iterable


def read_file(fp: str) -> str:
    with open(fp, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def normalize_whitespace(s: str) -> str:
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()