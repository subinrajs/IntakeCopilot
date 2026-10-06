"""The clinic's protocol book (data/protocols.yaml): typed loading and retrieval documents."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Protocol(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^(MRI|CT)-[A-Z0-9-]+$")
    modality: Literal["MRI", "CT"]
    body_part: str
    name: str
    contrast: Literal["none", "iv", "optional"]
    slot_minutes: int = Field(gt=0, le=120)
    indications: list[str] = Field(min_length=1)

    def retrieval_document(self) -> str:
        """The text embedded for retrieval: everything a radiologist would match on."""
        contrast = {
            "none": "without contrast",
            "iv": "with IV contrast",
            "optional": "contrast optional",
        }
        lines = [
            f"{self.name} ({self.modality}, {self.body_part}, {contrast[self.contrast]})",
            "Indications:",
            *(f"- {indication}" for indication in self.indications),
        ]
        return "\n".join(lines)


class ProtocolBook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    protocols: list[Protocol]

    @field_validator("protocols")
    @classmethod
    def ids_unique_and_prefixed(cls, protocols: list[Protocol]) -> list[Protocol]:
        ids = [p.id for p in protocols]
        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates:
            raise ValueError(f"duplicate protocol ids: {sorted(duplicates)}")
        for p in protocols:
            if not p.id.startswith(f"{p.modality}-"):
                raise ValueError(f"{p.id}: id prefix must match modality {p.modality}")
        return protocols


def load_protocol_book(path: Path) -> ProtocolBook:
    return ProtocolBook.model_validate(yaml.safe_load(path.read_text()))
