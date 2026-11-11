"""Pinned original evaluation document, never a generated answer/prompt."""
from pathlib import Path
from .section_semantic_review import ReviewContract

HERE=Path(__file__).resolve().parents[2]
SUBMISSION_GOLDEN_SHA256='602c000cec919584427415c434624695a939fd9638ef30df861721ace717b21f'


def submission_contract(case_id='M2-S03'):
    path=HERE/'schemas/submission-review-golden.json'
    if path.is_symlink() or path.resolve()!=HERE.resolve()/'schemas/submission-review-golden.json':
        raise ValueError('review_contract')
    return ReviewContract.from_document(path.read_bytes(),SUBMISSION_GOLDEN_SHA256,case_id)
