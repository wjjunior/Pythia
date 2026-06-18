"""Tests for the pure chunker."""

from pythia.rag import chunk


def test_short_text_is_a_single_chunk() -> None:
    assert chunk("hello world", max_chars=100) == ["hello world"]


def test_empty_text_yields_no_chunks() -> None:
    assert chunk("   ") == []


def test_long_text_is_split_with_overlap() -> None:
    text = "".join(str(i % 10) for i in range(250))
    chunks = chunk(text, max_chars=100, overlap=20)
    assert len(chunks) == 3
    assert all(len(piece) <= 100 for piece in chunks)
    assert chunks[0][-20:] == chunks[1][:20]
    assert chunks[-1].endswith(text[-1])
