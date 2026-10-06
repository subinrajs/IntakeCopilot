"""Step 1 output: structured fields read from the requisition, each with evidence.

`Extracted[T]` is the design document's `Field[T]`, renamed so it does not shadow
`pydantic.Field`. Every model is strict (no extra keys, every key present) so it can be used
directly as an OpenAI Structured Outputs schema.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Confidence = Literal["high", "medium", "low"]
Laterality = Literal["left", "right", "bilateral", "none"]
Modality = Literal["MRI", "CT"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(Strict):
    quote: str = Field(description="Exact text copied from the requisition, verbatim.")
    page: int = Field(description="1-based page number the quote appears on.")


class Extracted[T](Strict):
    value: T | None
    confidence: Confidence
    evidence: Evidence | None


class RequisitionExtraction(Strict):
    patient_name: Extracted[str]
    dob: Extracted[date]
    health_card_last4: Extracted[str] = Field(
        description="Only the last 4 digits of the health card number, never the full number."
    )
    referrer_name: Extracted[str]
    referrer_billing_number: Extracted[str]
    modality: Extracted[Modality]
    body_part: Extracted[str]
    laterality: Extracted[Laterality]
    contrast_requested: Extracted[bool]
    clinical_indication: Extracted[str]
    relevant_history: list[Extracted[str]]
    allergies: list[Extracted[str]]
    egfr: Extracted[float]
    egfr_date: Extracted[date]
    medications_of_note: list[Extracted[str]]
    physician_marked_urgent: Extracted[bool]


SCALAR_FIELDS: tuple[str, ...] = (
    "patient_name",
    "dob",
    "health_card_last4",
    "referrer_name",
    "referrer_billing_number",
    "modality",
    "body_part",
    "laterality",
    "contrast_requested",
    "clinical_indication",
    "egfr",
    "egfr_date",
    "physician_marked_urgent",
)
LIST_FIELDS: tuple[str, ...] = ("relevant_history", "allergies", "medications_of_note")
REQUIRED_FIELDS: tuple[str, ...] = (
    "patient_name",
    "dob",
    "modality",
    "body_part",
    "clinical_indication",
)
