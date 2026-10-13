"""S2 chunking: exact coverage, size limit, preferred boundaries, determinism."""
from __future__ import annotations

from secondbrain.pipelines.chunking import chunk_text


def spans(text, limit):
    chunks = chunk_text(text, limit)
    assert ''.join(text[c['start']:c['end']] for c in chunks) == text
    assert all(c['end'] - c['start'] <= limit for c in chunks)
    assert [c['chunk_id'] for c in chunks] == [f'c{i:02d}' for i in range(1, len(chunks) + 1)]
    assert chunk_text(text, limit) == chunks
    return chunks


def test_short_text_is_one_chunk():
    assert spans('# t\n\nbody\n', 100) == [{'chunk_id': 'c01', 'start': 0, 'end': 10, 'split_reason': 'whole'}]
    assert spans('', 100)[0]['end'] == 0


def test_headings_start_new_chunks_when_the_current_one_is_half_full():
    section = 'Sentence one. ' * 4
    text = f'# A\n\n{section}\n\n## B\n\n{section}\n\n## C\n\n{section}\n'
    chunks = spans(text, 100)
    starts = [text[c['start']:c['end']].lstrip().split('\n', 1)[0] for c in chunks]
    assert starts == ['# A', '## B', '## C']
    assert [c['split_reason'] for c in chunks] == ['heading', 'heading', 'heading']


def test_long_paragraph_splits_at_sentence_ends_then_hard_limit():
    zh = '句子很长。' * 40
    chunks = spans(zh + '\n\n' + 'x' * 250, 100)
    texts = [zh_text for zh_text in (zh[c['start']:c['end']] for c in chunks if c['end'] <= len(zh))]
    assert all(t.endswith('。') for t in texts if t)
    reasons = {c['split_reason'] for c in chunks}
    assert 'sentence' in reasons and 'hard_limit' in reasons


def test_small_paragraphs_are_merged_up_to_the_limit():
    text = ''.join(f'Paragraph {i}.\n\n' for i in range(30))
    chunks = spans(text, 120)
    assert len(chunks) < 30 and all(c['end'] - c['start'] > 60 for c in chunks[:-1])
