"""Point-in-time chunking: split evidence text into overlapping windows (§4.5).

Chunking is pure text work; the caller attaches `observed_at` when it builds the row, so a
chunk can never outlive the point in time at which its source was observed.
"""


def chunk(text: str, *, max_chars: int = 1000, overlap: int = 100) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    if len(stripped) <= max_chars:
        return [stripped]
    if overlap >= max_chars:
        raise ValueError("overlap must be smaller than max_chars")
    step = max_chars - overlap
    chunks: list[str] = []
    start = 0
    while start < len(stripped):
        chunks.append(stripped[start : start + max_chars])
        if start + max_chars >= len(stripped):
            break
        start += step
    return chunks
