"""PDFium is not thread-safe: rendering from several threads at once must not crash."""

from concurrent.futures import ThreadPoolExecutor

from intake.documents.pages import render_document
from intake.settings import get_settings


def test_concurrent_rendering_is_serialised_safely() -> None:
    pdfs = [
        (get_settings().data_dir / "gold" / "v1" / "pdfs" / f"gold-v1-{n:03d}.pdf").read_bytes()
        for n in (1, 2, 5, 9, 12, 20)
    ]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda d: render_document(d, "application/pdf"), pdfs * 3))
    assert all(r and r[0].text.spans for r in results)
