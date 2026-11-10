"""Splits text into short TTS chunks along sentence boundaries."""
import re

TARGET_CHARS = 250
MAX_CHARS = 350

ABBREVIATIONS = {
    "sr", "sra", "srta", "dr", "dra", "prof", "profa", "p", "pp", "pág", "págs", "ex", "etc",
    "vol", "cap", "fig", "nº", "n", "art", "inc", "av", "jr", "mr", "mrs", "ms", "st", "vs",
    "e.g", "i.e", "cf", "op", "obs", "ed", "eds", "séc", "aprox",
}
SENTENCE_END = re.compile(r"[.!?…]+[\"'”’»)\]]*\s+(?=[\"'“‘«(\[—–-]?[A-ZÀ-Ý0-9])")
SOFT_BREAK = re.compile(r"[;:,]\s+")


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    spans, start = [], 0
    for m in SENTENCE_END.finditer(text):
        before = text[start:m.start() + 1].rstrip().rstrip(".")
        word = before.split()[-1].lower() if before.strip() else ""
        if text[m.start()] == "." and (word in ABBREVIATIONS or (len(word) == 1 and word.isalpha())):
            continue
        end = m.end()
        spans.append((start, end))
        start = end
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def _split_long(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Cuts a span longer than MAX_CHARS at weak punctuation, then at spaces."""
    out = []
    while end - start > MAX_CHARS:
        window = text[start:start + MAX_CHARS]
        cut = None
        for m in SOFT_BREAK.finditer(window):
            if m.end() >= TARGET_CHARS * 0.4:
                cut = m.end()
        if cut is None:
            cut = window.rfind(" ")
            cut = cut + 1 if cut > 0 else MAX_CHARS
        out.append((start, start + cut))
        start += cut
    out.append((start, end))
    return out


def chunk_paragraph(text: str) -> list[tuple[int, int]]:
    pieces = []
    for s, e in _sentence_spans(text):
        pieces.extend(_split_long(text, s, e) if e - s > MAX_CHARS else [(s, e)])
    chunks, cur = [], None
    for s, e in pieces:
        if cur is None:
            cur = [s, e]
        elif e - cur[0] <= TARGET_CHARS or (e - cur[0] <= MAX_CHARS and cur[1] - cur[0] < TARGET_CHARS * 0.5):
            cur[1] = e
        else:
            chunks.append(tuple(cur))
            cur = [s, e]
    if cur:
        chunks.append(tuple(cur))
    return chunks


def chunk_plain_text(text: str) -> tuple[list[str], list[int]]:
    """Free text (paragraphs separated by blank lines) -> (chunks, paragraph index of each chunk)."""
    texts, paras = [], []
    for pno, para in enumerate(p for p in re.split(r"\n\s*\n", text) if p.strip()):
        para = re.sub(r"\s+", " ", para).strip()
        for s, e in chunk_paragraph(para):
            piece = para[s:e].strip()
            if re.search(r"\w", piece):
                texts.append(piece)
                paras.append(pno)
    return texts, paras
