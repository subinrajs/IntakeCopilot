from collections import Counter

import pytest
from pydantic import ValidationError

from intake.protocols import ProtocolBook, load_protocol_book
from intake.settings import get_settings


@pytest.fixture(scope="module")
def book() -> ProtocolBook:
    return load_protocol_book(get_settings().data_dir / "protocols.yaml")


def test_book_has_about_thirty_protocols_across_both_modalities(book: ProtocolBook) -> None:
    counts = Counter(p.modality for p in book.protocols)
    assert len(book.protocols) == 30
    assert counts["MRI"] >= 10 and counts["CT"] >= 8


def test_every_contrast_setting_is_represented(book: ProtocolBook) -> None:
    assert {p.contrast for p in book.protocols} == {"none", "iv", "optional"}


def test_design_doc_example_indications_are_covered(book: ProtocolBook) -> None:
    text = " ".join(i.lower() for p in book.protocols for i in p.indications)
    for phrase in ("new seizure in adult", "ms follow-up", "pituitary adenoma"):
        assert phrase in text


def test_retrieval_document_contains_name_and_indications(book: ProtocolBook) -> None:
    protocol = next(p for p in book.protocols if p.id == "MRI-L-SPINE")
    doc = protocol.retrieval_document()
    assert "MRI lumbar spine without contrast" in doc
    assert "- suspected cauda equina syndrome" in doc


def _entry(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "MRI-X",
        "modality": "MRI",
        "body_part": "x",
        "name": "x",
        "contrast": "none",
        "slot_minutes": 30,
        "indications": ["x"],
    }
    return base | overrides


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        ProtocolBook.model_validate({"version": 1, "protocols": [_entry(), _entry()]})


def test_id_prefix_must_match_modality() -> None:
    with pytest.raises(ValidationError, match="prefix"):
        ProtocolBook.model_validate({"version": 1, "protocols": [_entry(modality="CT")]})


def test_unknown_contrast_value_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ProtocolBook.model_validate({"version": 1, "protocols": [_entry(contrast="oral")]})
