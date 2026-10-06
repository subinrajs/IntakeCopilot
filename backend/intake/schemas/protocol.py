"""Step 3 output: one protocol chosen from the retrieved candidates.

The model class is built per call so `protocol_id` is a Literal of exactly the candidate ids:
an invented protocol cannot be expressed in the schema the model must follow.
"""

from typing import Any, Literal, cast

from pydantic import create_model

from intake.schemas.extraction import Confidence, Strict

ContrastChoice = Literal["none", "iv", "optional"]


class ProtocolChoice(Strict):
    protocol_id: str
    contrast: ContrastChoice
    rationale: str
    confidence: Confidence


def constrained_protocol_choice(candidate_ids: list[str]) -> type[ProtocolChoice]:
    if not candidate_ids:
        raise ValueError("at least one candidate protocol is required")
    id_type = Literal[tuple(candidate_ids)]  # type: ignore[valid-type]
    model = create_model(
        "ProtocolChoice",
        __base__=ProtocolChoice,
        protocol_id=(cast(Any, id_type), ...),
    )
    return model
