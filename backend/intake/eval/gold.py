"""Gold-set labels (data/gold/<version>/cases.yaml): the ground truth every metric uses."""

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from intake.schemas.extraction import Laterality, Modality
from intake.schemas.triage import Priority


class GoldFields(BaseModel):
    """Plain expected values for every extraction field (no evidence or confidence)."""

    model_config = ConfigDict(extra="forbid")

    patient_name: str
    dob: date
    health_card_last4: str
    referrer_name: str
    referrer_billing_number: str
    modality: Modality
    body_part: str
    laterality: Laterality
    contrast_requested: bool
    clinical_indication: str
    relevant_history: list[str]
    allergies: list[str]
    egfr: float | None
    egfr_date: date | None
    medications_of_note: list[str]
    physician_marked_urgent: bool


class GoldCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_key: str
    file: str  # relative to the gold version directory
    difficulty: Literal["clean", "hard"]
    noise: Literal["none", "light", "heavy"]
    layout: Literal["form", "letter", "fax"]
    as_of: date
    priority: Priority
    protocol_id: str
    contrast_flags: list[str]
    fields: GoldFields
    notes: str = ""


class GoldSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    labelled_by: str
    cases: list[GoldCase]


def gold_dir(data_dir: Path, version: str) -> Path:
    return data_dir / "gold" / version


def load_gold_set(data_dir: Path, version: str) -> GoldSet:
    path = gold_dir(data_dir, version) / "cases.yaml"
    return GoldSet.model_validate(yaml.safe_load(path.read_text()))
