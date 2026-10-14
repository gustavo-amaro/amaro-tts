"""Extração de texto de PDF, limpeza e divisão em trechos para TTS."""
import re
import sys
import time
import unicodedata
from collections import Counter

import pymupdf as fitz

TARGET_CHARS = 250
MAX_CHARS = 350

LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
             "­": "", "​": ""}
ABBREVIATIONS = {
    "sr", "sra", "srta", "dr", "dra", "prof", "profa", "p", "pp", "pág", "págs", "ex", "etc",
    "vol", "cap", "fig", "nº", "n", "art", "inc", "av", "jr", "mr", "mrs", "ms", "st", "vs",
    "e.g", "i.e", "cf", "op", "obs", "ed", "eds", "séc", "aprox",
}
SENTENCE_END = re.compile(r"[.!?…]+[\"'”’»)\]]*\s+(?=[\"'“‘«(\[—–-]?[A-ZÀ-Ý0-9])")
SOFT_BREAK = re.compile(r"[;:,]\s+")
PAGE_NUMBER = re.compile(r"^\s*(\d{1,4}|[ivxlcdm]{1,6})\s*$", re.IGNORECASE)


def _clean_block(text: str) -> str:
    for k, v in LIGATURES.items():
        text = text.replace(k, v)
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # palavra hifenizada no fim da linha
    text = re.sub(r"\s*\n\s*", " ", text)
    return re.sub(r"[ \t ]+", " ", text).strip()


def _signature(text: str) -> str:
    """Forma normalizada para detectar cabeçalhos/rodapés repetidos (ignora números)."""
    return re.sub(r"\d+", "#", text.lower())[:80]


def extract_blocks(path: str, progress=None) -> list[list[str]]:
    """Retorna, por página, a lista de blocos de texto limpos."""
    pages = []
    with fitz.open(path) as doc:
        n = doc.page_count
        for i, page in enumerate(doc):
            blocks = page.get_text("blocks", sort=True)
            pages.append([_clean_block(b[4]) for b in blocks if b[6] == 0])
            if progress and (i % 10 == 0 or i == n - 1):
                progress(i + 1, n)
    return pages


def remove_headers(pages: list[list[str]]) -> list[list[str]]:
    n = len(pages)
    counts = Counter()
    for blocks in pages:
        # só as bordas da página costumam ser cabeçalho/rodapé
        edge = {_signature(b) for b in blocks[:2] + blocks[-2:] if len(b) < 100}
        counts.update(edge)
    threshold = max(3, int(n * 0.3))
    repeated = {s for s, c in counts.items() if c >= threshold}
    out = []
    for blocks in pages:
        kept = []
        for idx, b in enumerate(blocks):
            at_edge = idx < 2 or idx >= len(blocks) - 2
            if not b or PAGE_NUMBER.match(b):
                continue
            if at_edge and len(b) < 100 and _signature(b) in repeated:
                continue
            kept.append(b)
        out.append(kept)
    return out


def merge_paragraphs(blocks: list[str]) -> list[str]:
    """Junta blocos que continuam a mesma frase (bloco anterior sem pontuação final)."""
    paras = []
    for b in blocks:
        if paras and not re.search(r"[.!?…:\"”»)]$", paras[-1]) and b[:1].islower():
            prev = paras[-1]
            paras[-1] = prev[:-1] + b if prev.endswith("-") else prev + " " + b
        else:
            paras.append(b)
    return paras


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
    """Corta um trecho maior que MAX_CHARS em pontuação fraca, depois em espaços."""
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


def build_document(path: str, progress=None) -> dict:
    """Extrai e divide o PDF. Retorna {"pages": [{"text", "first", "last"}], "chunks": [...]}."""
    pages = remove_headers(extract_blocks(path, progress))
    out_pages, chunks = [], []
    for pno, blocks in enumerate(pages):
        paras = merge_paragraphs(blocks)
        page_text = "\n\n".join(paras)
        first = len(chunks)
        offset = 0
        for para in paras:
            for s, e in chunk_paragraph(para):
                # mantém offsets sem espaços nas bordas
                while s < e and para[s].isspace():
                    s += 1
                while e > s and para[e - 1].isspace():
                    e -= 1
                piece = para[s:e]
                if not re.search(r"\w{2,}", piece):
                    continue
                chunks.append({"page": pno, "start": offset + s, "end": offset + e, "text": piece})
            offset += len(para) + 2
        out_pages.append({"text": page_text, "first": first, "last": len(chunks) - 1})
    return {"pages": out_pages, "chunks": chunks}


if __name__ == "__main__":
    t0 = time.time()
    doc = build_document(sys.argv[1], lambda i, n: print(f"\r{i}/{n}", end="", file=sys.stderr))
    print(f"\npáginas={len(doc['pages'])} trechos={len(doc['chunks'])} tempo={time.time() - t0:.1f}s")
    lens = [len(c["text"]) for c in doc["chunks"]]
    if lens:
        print(f"chars: min={min(lens)} média={sum(lens) / len(lens):.0f} max={max(lens)}")
    for c in doc["chunks"][: int(sys.argv[2]) if len(sys.argv) > 2 else 8]:
        print(f"[p{c['page'] + 1}] {c['text']!r}")
