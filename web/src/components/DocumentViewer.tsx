import { cx } from "./ui";

export interface Highlight {
  page: number;
  boxes: [number, number, number, number][];
  strong?: boolean;
}

/** Page images with evidence boxes drawn as an SVG overlay (pixel coordinates of the
 * rendered page, so the same boxes work for text-layer PDFs and OCR'd scans). */
export function DocumentViewer({
  pages,
  imageUrl,
  highlights,
}: {
  pages: { page_no: number; width: number; height: number; text_source: string }[];
  imageUrl: (page: number) => string;
  highlights: Highlight[];
}) {
  if (pages.length === 0) {
    return (
      <div className="grid h-64 place-items-center rounded-lg border border-line bg-surface text-sm text-ink-2">
        No page images (the file could not be read).
      </div>
    );
  }
  return (
    <div className="space-y-3">
      {pages.map((page) => {
        const boxes = highlights.filter((h) => h.page === page.page_no);
        return (
          <figure key={page.page_no} className="overflow-hidden rounded-lg border border-line bg-white">
            <div className="relative">
              <img
                src={imageUrl(page.page_no)}
                alt={`Requisition page ${page.page_no}`}
                className="block w-full"
                loading="lazy"
              />
              <svg
                className="pointer-events-none absolute inset-0 size-full"
                viewBox={`0 0 ${page.width} ${page.height}`}
                preserveAspectRatio="none"
                aria-hidden
              >
                {boxes.flatMap((h, i) =>
                  h.boxes.map(([x0, y0, x1, y1], j) => (
                    <rect
                      key={`${i}-${j}`}
                      x={x0 - 3}
                      y={y0 - 3}
                      width={x1 - x0 + 6}
                      height={y1 - y0 + 6}
                      rx={3}
                      fill={h.strong ? "var(--highlight-strong)" : "var(--highlight)"}
                      stroke={h.strong ? "var(--accent)" : "none"}
                      strokeWidth={h.strong ? 2 : 0}
                    />
                  )),
                )}
              </svg>
            </div>
            <figcaption
              className={cx("border-t border-line bg-surface px-3 py-1 text-[11px] text-ink-2")}
            >
              Page {page.page_no} · text from {page.text_source === "ocr" ? "OCR (scan)" : "PDF text layer"}
            </figcaption>
          </figure>
        );
      })}
    </div>
  );
}
