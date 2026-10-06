"""Render uploaded requisitions to page images and build a text index for every page.

The text index is a list of spans (a line or text run with its pixel box). It comes from the
PDF text layer when the page has one and from OCR otherwise (scans and faxes). It is used to
check evidence quotes, to draw highlight boxes in the review screen and to run the red-flag
rules over the whole document, not just the extracted fields (ADR 0003).
"""

import io
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

import pypdfium2 as pdfium
from PIL import Image

RENDER_DPI = 150
OCR_DPI = 200
MIN_TEXT_LAYER_CHARS = 40  # fewer than this and the page is treated as a scan
MAX_PAGES = 10

TextSource = Literal["text_layer", "ocr"]

# PDFium is not thread-safe, and the worker thread, eval runs and the API can all render. Every
# use of PDFium (and the shared OCR engine) in this process goes through this lock.
PDFIUM_LOCK = threading.RLock()


@dataclass(frozen=True)
class Span:
    text: str
    box: tuple[int, int, int, int]  # x0, y0, x1, y1 in rendered-image pixels, origin top-left

    def to_json(self) -> dict[str, Any]:
        return {"t": self.text, "b": list(self.box)}


@dataclass
class PageText:
    source: TextSource
    spans: list[Span] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(s.text for s in self.spans)


@dataclass
class RenderedPage:
    page_no: int
    png: bytes
    width: int
    height: int
    text: PageText


class UnreadableDocument(Exception):
    """The file cannot be opened or rendered (corrupt, encrypted, unsupported)."""


@lru_cache(maxsize=1)
def _ocr_engine() -> Any:
    from rapidocr import RapidOCR

    return RapidOCR(params={"Global.log_level": "warning"})


def _png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def ocr_spans(image: Image.Image, scale_to_render: float) -> list[Span]:
    """OCR an image; boxes are scaled into rendered-image pixel space."""
    result = _ocr_engine()(image.convert("RGB"))
    spans: list[Span] = []
    if result.boxes is None:
        return spans
    for box, text in zip(result.boxes, result.txts, strict=True):
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        spans.append(
            Span(
                text=str(text),
                box=(
                    round(min(xs) * scale_to_render),
                    round(min(ys) * scale_to_render),
                    round(max(xs) * scale_to_render),
                    round(max(ys) * scale_to_render),
                ),
            )
        )
    spans.sort(key=lambda s: (round(s.box[1] / 8), s.box[0]))  # reading order
    return spans


def _text_layer_spans(page: pdfium.PdfPage, scale: float) -> list[Span] | None:
    textpage = page.get_textpage()
    try:
        if len(textpage.get_text_range().strip()) < MIN_TEXT_LAYER_CHARS:
            return None
        height = page.get_height()
        spans: list[Span] = []
        for i in range(textpage.count_rects()):
            left, bottom, right, top = textpage.get_rect(i)
            text = textpage.get_text_bounded(left, bottom, right, top).strip()
            if not text:
                continue
            spans.append(
                Span(
                    text=" ".join(text.split()),
                    box=(
                        round(left * scale),
                        round((height - top) * scale),
                        round(right * scale),
                        round((height - bottom) * scale),
                    ),
                )
            )
        return spans
    finally:
        textpage.close()


def render_pdf(data: bytes) -> list[RenderedPage]:
    try:
        document = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as error:
        raise UnreadableDocument(str(error)) from error
    try:
        if len(document) == 0:
            raise UnreadableDocument("PDF has no pages")
        if len(document) > MAX_PAGES:
            raise UnreadableDocument(f"PDF has more than {MAX_PAGES} pages")
        pages: list[RenderedPage] = []
        scale = RENDER_DPI / 72
        for index, page in enumerate(document, start=1):
            image = page.render(scale=scale).to_pil().convert("RGB")
            spans = _text_layer_spans(page, scale)
            if spans is not None:
                text = PageText("text_layer", spans)
            else:
                ocr_image = page.render(scale=OCR_DPI / 72).to_pil()
                text = PageText("ocr", ocr_spans(ocr_image, RENDER_DPI / OCR_DPI))
            pages.append(RenderedPage(index, _png(image), image.width, image.height, text))
            page.close()
        return pages
    finally:
        document.close()


def render_image(data: bytes) -> list[RenderedPage]:
    try:
        opened = Image.open(io.BytesIO(data))
        opened.load()
    except Exception as error:
        raise UnreadableDocument(str(error)) from error
    image: Image.Image = opened.convert("RGB")
    if image.width > 2000:  # normalise phone photos and high-DPI scans
        ratio = 2000 / image.width
        image = image.resize((2000, round(image.height * ratio)))
    text = PageText("ocr", ocr_spans(image, 1.0))
    return [RenderedPage(1, _png(image), image.width, image.height, text)]


def render_document(data: bytes, content_type: str) -> list[RenderedPage]:
    with PDFIUM_LOCK:
        if content_type == "application/pdf":
            return render_pdf(data)
        if content_type in ("image/png", "image/jpeg"):
            return render_image(data)
    raise UnreadableDocument(f"unsupported content type {content_type}")
