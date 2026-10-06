"""Effective case fields: the AI extraction with human corrections applied on top.

Downstream steps (triage, protocol, contrast rules), the review screen and the HL7 export all
read effective fields, so a correction made by staff is what the rules and export see. The AI
value stays recoverable in pipeline_steps, and each correction is its own row.
"""

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, TypeAdapter

from intake.schemas.extraction import (
    LIST_FIELDS,
    SCALAR_FIELDS,
    Laterality,
    Modality,
    RequisitionExtraction,
)


class CaseFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient_name: str | None = None
    dob: date | None = None
    health_card_last4: str | None = None
    referrer_name: str | None = None
    referrer_billing_number: str | None = None
    modality: Modality | None = None
    body_part: str | None = None
    laterality: Laterality | None = None
    contrast_requested: bool | None = None
    clinical_indication: str | None = None
    relevant_history: list[str] = []
    allergies: list[str] = []
    egfr: float | None = None
    egfr_date: date | None = None
    medications_of_note: list[str] = []
    physician_marked_urgent: bool | None = None


EDITABLE_FIELDS: frozenset[str] = frozenset(SCALAR_FIELDS + LIST_FIELDS)


def from_extraction(extraction: RequisitionExtraction | None) -> CaseFields:
    if extraction is None:
        return CaseFields()
    values: dict[str, Any] = {}
    for name in SCALAR_FIELDS:
        values[name] = getattr(extraction, name).value
    for name in LIST_FIELDS:
        values[name] = [item.value for item in getattr(extraction, name) if item.value]
    return CaseFields.model_validate(values)


def validate_value(field: str, value: Any) -> Any:
    """Validate one corrected value against the field's type; returns the JSON-ready value."""
    if field not in EDITABLE_FIELDS:
        raise ValueError(f"unknown field {field}")
    annotation = CaseFields.model_fields[field].annotation
    adapter: TypeAdapter[Any] = TypeAdapter(annotation)
    return adapter.dump_python(adapter.validate_python(value), mode="json")


def apply_corrections(fields: CaseFields, corrections: list[tuple[str, Any]]) -> CaseFields:
    """Corrections in chronological order; the latest one per field wins."""
    data = fields.model_dump()
    for field, value in corrections:
        if field in EDITABLE_FIELDS:
            data[field] = value
    return CaseFields.model_validate(data)
