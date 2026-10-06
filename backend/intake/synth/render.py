"""Render a requisition to PDF in one of three layouts, then optionally degrade it like a scan.

- form:   the clinic's own requisition form (boxed sections, [X] checkboxes)
- letter: a referral letter from the family doctor's EMR (prose and lists)
- fax:    the form with a fax transmission header on every page

Noisy cases are rasterized and saved as image-only PDFs, so they have no text layer and the
pipeline has to OCR them, as it would a real fax.
"""

import io
import random
from pathlib import Path
from typing import Any

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Flowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from intake.documents.pages import PDFIUM_LOCK
from intake.synth.build import Requisition

CLINIC = "LAKESHORE MRI &amp; CT"
CLINIC_ADDRESS = (
    "1200 Lakeshore Road West, Mississauga, ON  ·  Tel 905-555-0100  ·  Fax 905-555-0101"
)

_styles = getSampleStyleSheet()
BODY = ParagraphStyle(
    "body", parent=_styles["Normal"], fontName="Helvetica", fontSize=9.5, leading=12.5
)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8, leading=10, textColor=colors.grey)
LABEL = ParagraphStyle("label", parent=BODY, fontName="Helvetica-Bold", fontSize=8.5)
TITLE = ParagraphStyle("title", parent=BODY, fontName="Helvetica-Bold", fontSize=15, leading=18)
SECTION = ParagraphStyle(
    "section", parent=BODY, fontName="Helvetica-Bold", fontSize=9, textColor=colors.white
)


def _box(mark: bool) -> str:
    return "[X]" if mark else "[  ]"


def _dob_text(r: Requisition) -> tuple[str, str]:
    if r.scenario.dob_day_first:
        return "Date of birth (DD/MM/YYYY)", r.dob.strftime("%d/%m/%Y")
    return "Date of birth", r.dob.isoformat()


def _lines(items: tuple[str, ...], empty: str) -> str:
    return "<br/>".join(f"• {i}" for i in items) if items else empty


def _section(title: str, rows: list[list[Any]], widths: list[float]) -> list[Flowable]:
    header = Table([[Paragraph(title, SECTION)]], colWidths=[sum(widths)])
    header.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1f4e5f")),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    body = Table(rows, colWidths=widths)
    body.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#8a9ba3")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return [header, body, Spacer(1, 6)]


def _p(text: str, style: ParagraphStyle = BODY) -> Paragraph:
    return Paragraph(text, style)


def _form_story(r: Requisition) -> list[Flowable]:
    s = r.scenario
    w = [1.45 * inch, 2.3 * inch, 1.25 * inch, 2.3 * inch]
    dob_label, dob_value = _dob_text(r)
    lat = {k: _box(s.laterality == k) for k in ("left", "right", "bilateral", "none")}
    story: list[Flowable] = [
        _p(CLINIC, TITLE),
        _p("Diagnostic Imaging Requisition  ·  MRI / CT", LABEL),
        _p(CLINIC_ADDRESS, SMALL),
        Spacer(1, 8),
    ]
    story += _section(
        "PATIENT",
        [
            [
                _p("Name (surname, given)", LABEL),
                _p(f"{r.surname.upper()}, {r.given_name}"),
                _p(dob_label, LABEL),
                _p(dob_value),
            ],
            [_p("Health card no.", LABEL), _p(r.health_card), _p("Sex", LABEL), _p(r.sex)],
            [_p("Address", LABEL), _p(r.patient_address), _p("Phone", LABEL), _p(r.patient_phone)],
        ],
        w,
    )
    story += _section(
        "EXAMINATION REQUESTED",
        [
            [
                _p("Modality", LABEL),
                _p(f"{_box(s.modality == 'MRI')} MRI &nbsp;&nbsp; {_box(s.modality == 'CT')} CT"),
                _p("URGENT", LABEL),
                _p(f"{_box(s.urgent)} Urgent"),
            ],
            [
                _p("Body part / exam", LABEL),
                _p(s.exam),
                _p("Contrast", LABEL),
                _p(f"{_box(s.contrast)} Yes &nbsp;&nbsp; {_box(not s.contrast)} No"),
            ],
            [
                _p("Laterality", LABEL),
                _p(
                    f"{lat['left']} Left &nbsp; {lat['right']} Right &nbsp; {lat['bilateral']} "
                    f"Bilateral &nbsp; {lat['none']} N/A"
                ),
                _p(""),
                _p(""),
            ],
        ],
        w,
    )
    story += _section(
        "CLINICAL INFORMATION",
        [
            [_p("Clinical indication", LABEL), _p(s.indication)],
            [_p("Relevant history", LABEL), _p(_lines(s.history, "None"))],
            [_p("Allergies", LABEL), _p(_lines(s.allergies, "No known allergies"))],
        ],
        [w[0], sum(w[1:])],
    )
    if s.page_break_before_meds:
        story.append(PageBreak())
    story += _section(
        "MEDICATIONS AND RENAL FUNCTION (required for contrast)",
        [
            [_p("Current medications", LABEL), _p(_lines(s.meds, "None"))],
            [
                _p("eGFR (mL/min/1.73m²)", LABEL),
                _p(f"{s.egfr:g}" if s.egfr is not None else "____"),
                _p("Date of eGFR", LABEL),
                _p(r.egfr_date.isoformat() if r.egfr_date else "____"),
            ],
        ],
        [w[0], w[1], w[2], w[3]],
    )
    if s.comments:
        story += _section("ADDITIONAL COMMENTS", [[_p(s.comments)]], [sum(w)])
    story += _section(
        "REFERRING PHYSICIAN",
        [
            [
                _p("Name", LABEL),
                _p(f"Dr. {r.referrer_name}"),
                _p("Billing no.", LABEL),
                _p(r.billing_number),
            ],
            [
                _p("Clinic", LABEL),
                _p(r.clinic),
                _p("Phone / fax", LABEL),
                _p(f"{r.clinic_phone} / {r.clinic_fax}"),
            ],
            [
                _p("Signature", LABEL),
                _p("<i>(signed electronically)</i>"),
                _p("Date", LABEL),
                _p(r.received.isoformat()),
            ],
        ],
        w,
    )
    return story


def _letter_story(r: Requisition) -> list[Flowable]:
    s = r.scenario
    dob_label, dob_value = _dob_text(r)
    side = "" if s.laterality == "none" else f" ({s.laterality})"
    contrast = "with contrast" if s.contrast else "without contrast"
    article = "an" if s.modality == "MRI" else "a"
    story: list[Flowable] = [
        _p(r.clinic, TITLE),
        _p(f"Tel {r.clinic_phone}  ·  Fax {r.clinic_fax}", SMALL),
        Spacer(1, 14),
        _p(r.received.strftime("%B %-d, %Y")),
        Spacer(1, 8),
        _p("To: Lakeshore MRI &amp; CT, Fax 905-555-0101"),
        Spacer(1, 8),
        _p(
            f"<b>Re: {r.given_name} {r.surname}</b> &nbsp;·&nbsp; {dob_label}: {dob_value} "
            f"&nbsp;·&nbsp; Health card: {r.health_card} &nbsp;·&nbsp; Sex: {r.sex}"
        ),
        Spacer(1, 10),
        _p("Dear Colleague,"),
        Spacer(1, 6),
        _p(
            f"Thank you for seeing this patient. I am requesting {article} {s.modality} of the "
            f"<b>{s.exam}</b>{side}, {contrast}."
        ),
        Spacer(1, 6),
    ]
    if s.urgent:
        story += [_p("<b>This request is URGENT.</b>"), Spacer(1, 6)]
    story += [
        _p(f"<b>Clinical question:</b> {s.indication}."),
        Spacer(1, 6),
        _p("<b>Relevant history:</b>"),
        _p(_lines(s.history, "None")),
        Spacer(1, 6),
        _p(f"<b>Allergies:</b> {'; '.join(s.allergies) if s.allergies else 'No known allergies'}"),
        Spacer(1, 6),
    ]
    if s.page_break_before_meds:
        story.append(PageBreak())
    story += [_p("<b>Current medications:</b>"), _p(_lines(s.meds, "None")), Spacer(1, 6)]
    if s.egfr is not None and r.egfr_date is not None:
        story.append(
            _p(
                f"<b>Renal function:</b> eGFR {s.egfr:g} mL/min/1.73m² on "
                f"{r.egfr_date.isoformat()}."
            )
        )
    else:
        story.append(_p("<b>Renal function:</b> not available."))
    if s.comments:
        story += [Spacer(1, 6), _p(f"<b>Comments:</b> {s.comments}")]
    story += [
        Spacer(1, 14),
        _p("Sincerely,"),
        Spacer(1, 16),
        _p(f"Dr. {r.referrer_name}, MD"),
        _p(f"Billing number {r.billing_number}"),
        _p(r.clinic),
    ]
    return story


def _fax_header(r: Requisition) -> Any:
    def draw(canvas: Canvas, doc: SimpleDocTemplate) -> None:
        canvas.saveState()
        canvas.setFont("Courier", 8)
        canvas.drawString(
            0.5 * inch,
            LETTER[1] - 0.35 * inch,
            f"FAX FROM {r.clinic_fax}   {r.received.isoformat()} 10:42   "
            f"TO 905-555-0101   P.{doc.page}",
        )
        canvas.restoreState()

    return draw


def render_pdf(r: Requisition) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        title="Imaging requisition",
        author="IntakeCopilot synthetic data",
        invariant=1,
    )
    story = _letter_story(r) if r.scenario.layout == "letter" else _form_story(r)
    if r.scenario.layout == "fax":
        doc.build(story, onFirstPage=_fax_header(r), onLaterPages=_fax_header(r))
    else:
        doc.build(story)
    return buffer.getvalue()


def degrade(pdf_bytes: bytes, level: str, seed: int) -> bytes:
    """Rasterize and add scan or fax artefacts; returns an image-only PDF."""
    rng = np.random.default_rng(seed)
    py_rng = random.Random(seed)
    pages: list[Image.Image] = []
    with PDFIUM_LOCK:
        document = pdfium.PdfDocument(pdf_bytes)
        rasters = [page.render(scale=200 / 72).to_pil().convert("L") for page in document]
        document.close()
    for image in rasters:
        if level == "heavy":
            # Fax "standard" resolution: half the vertical resolution, then 1-bit.
            w, h = image.size
            image = image.resize((w, h // 2)).resize((w, h), Image.Resampling.NEAREST)
            image = image.rotate(py_rng.uniform(-1.6, 1.6), fillcolor=255, expand=False)
            pixels = np.asarray(image, dtype=np.int16)
            pixels = pixels + rng.normal(0, 18, pixels.shape)
            binary = np.where(pixels > 150, 255, 0).astype(np.uint8)
            speckle = rng.random(binary.shape) < 0.002
            binary[speckle] = 255 - binary[speckle]
            for _ in range(py_rng.randint(1, 3)):  # transmission streaks
                y = py_rng.randint(0, binary.shape[0] - 3)
                binary[y : y + 2, :] = np.minimum(binary[y : y + 2, :], 90)
            image = Image.fromarray(binary)
        else:
            image = image.rotate(py_rng.uniform(-1.0, 1.0), fillcolor=250, expand=False)
            image = image.filter(ImageFilter.GaussianBlur(0.6))
            pixels = np.asarray(image, dtype=np.int16)
            pixels = pixels * 0.88 + 22 + rng.normal(0, 9, pixels.shape)
            image = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))
            jpeg = io.BytesIO()
            image.save(jpeg, format="JPEG", quality=55)
            image = Image.open(io.BytesIO(jpeg.getvalue())).convert("L")
        pages.append(image)
    out = io.BytesIO()
    pages[0].save(out, format="PDF", save_all=True, append_images=pages[1:], resolution=200.0)
    return out.getvalue()


def write_case_pdf(r: Requisition, path: Path, seed: int) -> None:
    pdf = render_pdf(r)
    if r.scenario.noise != "none":
        pdf = degrade(pdf, r.scenario.noise, seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf)
