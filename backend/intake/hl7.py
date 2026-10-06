"""Sample HL7 v2.3 ORM^O01 (new order) message for an approved case.

Not a live RIS integration (a non-goal): it shows the shape of the hand-off. Only data the
case holds is used; the health card is represented by its last 4 digits, as stored.
"""

from datetime import datetime
from typing import Any

PRIORITY_CODES = {"P1": "S", "P2": "A", "P3": "R", "P4": "R"}  # stat / ASAP / routine
MODALITY_CODES = {"MRI": "MR", "CT": "CT"}


def escape(value: Any) -> str:
    """HL7 escape sequences for the delimiter characters (\\ first)."""
    text = "" if value is None else str(value)
    for raw, escaped in (
        ("\\", "\\E\\"),
        ("|", "\\F\\"),
        ("^", "\\S\\"),
        ("~", "\\R\\"),
        ("&", "\\T\\"),
    ):
        text = text.replace(raw, escaped)
    return text.replace("\r", " ").replace("\n", " ")


def _name(full: str | None) -> str:
    parts = (full or "").split()
    if not parts:
        return ""
    return f"{escape(parts[-1].upper())}^{escape(' '.join(parts[:-1]))}"


def orm_message(
    *,
    case_id: str,
    fields: dict[str, Any],
    priority: str,
    protocol_id: str,
    protocol_name: str,
    contrast_flags: list[str],
    approved_by: str,
    now: datetime,
) -> str:
    ts = now.strftime("%Y%m%d%H%M%S")
    dob = (fields.get("dob") or "").replace("-", "")
    segments = [
        f"MSH|^~\\&|INTAKECOPILOT|LAKESHORE|RIS|LAKESHORE|{ts}||ORM^O01|{escape(case_id[:20])}|P|2.3",
        f"PID|1||{escape(fields.get('health_card_last4'))}^^^ONHCN-LAST4||"
        f"{_name(fields.get('patient_name'))}||{dob}|U",
        "PV1|1|O",
        f"ORC|NW|{escape(case_id)}||||||^^^^^{PRIORITY_CODES.get(priority, 'R')}||{ts}|"
        f"{escape(approved_by)}",
        f"OBR|1|{escape(case_id)}||{escape(protocol_id)}^{escape(protocol_name)}^L|"
        f"{PRIORITY_CODES.get(priority, 'R')}|{ts}|||||||"
        f"{escape(fields.get('clinical_indication'))}|||"
        f"{escape(fields.get('referrer_billing_number'))}^{_name(fields.get('referrer_name'))}"
        f"||||||||{MODALITY_CODES.get(fields.get('modality') or '', '')}",
        f"NTE|1||Priority {escape(priority)} approved by {escape(approved_by)}",
    ]
    for index, flag in enumerate(contrast_flags, start=2):
        segments.append(f"NTE|{index}||Contrast flag acknowledged: {escape(flag)}")
    return "\r".join(segments) + "\r"
