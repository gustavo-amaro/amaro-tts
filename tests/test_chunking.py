from app.text.chunking import MAX_CHARS, TARGET_CHARS, _sentence_spans, chunk_paragraph, chunk_plain_text


def sentence(n: int) -> str:
    """Sentence with exactly n characters, including the trailing ". "."""
    return "B" + "a" * (n - 3) + ". "


def assert_covers(text, chunks):
    assert chunks[0][0] == 0
    assert chunks[-1][1] == len(text)
    for (_, e), (s, _) in zip(chunks, chunks[1:]):
        assert e == s


def test_sentence_spans_splits_on_sentence_end():
    text = "Primeira frase. Segunda frase! Terceira?"
    assert [text[s:e] for s, e in _sentence_spans(text)] == ["Primeira frase. ", "Segunda frase! ", "Terceira?"]


def test_sentence_spans_ignores_abbreviations_and_initials():
    text = "O Dr. Silva e J. Souza chegaram. Fim."
    assert [text[s:e] for s, e in _sentence_spans(text)] == ["O Dr. Silva e J. Souza chegaram. ", "Fim."]


def test_sentence_spans_requires_uppercase_after_period():
    text = "valor 3.5 e depois. continua minúsculo"
    assert _sentence_spans(text) == [(0, len(text))]


def test_chunk_paragraph_empty():
    assert chunk_paragraph("") == []


def test_chunk_paragraph_short_text_is_single_chunk():
    text = "Uma frase. Outra frase."
    assert chunk_paragraph(text) == [(0, len(text))]


def test_chunk_paragraph_groups_short_sentences_up_to_target():
    text = "".join(sentence(60) for _ in range(20)).strip()
    chunks = chunk_paragraph(text)
    assert_covers(text, chunks)
    assert all(e - s <= TARGET_CHARS for s, e in chunks)
    assert len(chunks) > 1


def test_chunk_paragraph_merges_small_chunk_beyond_target():
    text = (sentence(100) + sentence(200)).strip()  # 299 > TARGET, but the first chunk is small
    assert chunk_paragraph(text) == [(0, len(text))]


def test_chunk_paragraph_does_not_merge_when_first_is_big_enough():
    text = (sentence(150) + sentence(190)).strip()
    assert chunk_paragraph(text) == [(0, 150), (150, len(text))]


def test_long_sentence_is_cut_at_soft_punctuation():
    text = ", ".join(["palavra " * 6] * 12).strip()
    chunks = chunk_paragraph(text)
    assert_covers(text, chunks)
    assert all(e - s <= MAX_CHARS for s, e in chunks)
    assert text[chunks[0][1] - 2:chunks[0][1]] == ", "


def test_long_sentence_without_punctuation_is_cut_at_spaces():
    text = " ".join(["palavra"] * 120)
    chunks = chunk_paragraph(text)
    assert_covers(text, chunks)
    assert all(text[e - 1] == " " for _, e in chunks[:-1])
    assert all(e - s <= MAX_CHARS for s, e in chunks)


def test_long_word_without_spaces_is_cut_hard():
    text = "a" * 800
    assert chunk_paragraph(text) == [(0, MAX_CHARS), (MAX_CHARS, 2 * MAX_CHARS), (2 * MAX_CHARS, 800)]


def test_chunk_plain_text_numbers_paragraphs_and_normalizes_spaces():
    text = "Primeiro   parágrafo.\nContinua.\n\n   \n\nSegundo.\n\n--- ..."
    texts, paras = chunk_plain_text(text)
    assert texts == ["Primeiro parágrafo. Continua.", "Segundo."]
    assert paras == [0, 1]


def test_chunk_plain_text_empty():
    assert chunk_plain_text("") == ([], [])
