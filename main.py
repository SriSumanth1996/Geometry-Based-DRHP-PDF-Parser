"""Extract the PDFs named below into one HTML review.

Run with: python main.py
Every PDF can stay in input/. Only ACTIVE_PDFS is extracted.
Set ACTIVE_PDFS = None to extract every PDF in input/ again.
One PDF writes output/<that file>.html. Several write output/drhp_collection.html.
Images, debug previews, and logs are stored under output/ in per-PDF folders.

TOC detection only labels Introductory Information and Table of Contents.
Pages after the TOC are Main Document. The source PDF is not modified.
A failed run leaves the previous package in place.
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
INPUT_DIR = HERE / "input"
OUTPUT_DIR = HERE / "output"

# None means every PDF in input/. A list means only those filenames.
ACTIVE_PDFS = None


def selected_pdfs():
    if ACTIVE_PDFS is None:
        return sorted(INPUT_DIR.glob("*.pdf"), key=lambda path: path.name.casefold())
    return [INPUT_DIR / name for name in ACTIVE_PDFS]


def main():
    from table_structure_ab import run_collection

    pdf_paths = selected_pdfs()
    missing = [path.name for path in pdf_paths if not path.is_file()]
    if missing:
        raise SystemExit(f"Missing PDF in {INPUT_DIR}: {', '.join(missing)}")
    if not pdf_paths:
        raise SystemExit(f"No PDF files found in {INPUT_DIR}")
    collection_name = "drhp_collection.html" if len(pdf_paths) > 1 else f"{pdf_paths[0].stem}.html"
    result = run_collection(
        pdf_paths=pdf_paths,
        output_dir=OUTPUT_DIR,
        collection_name=collection_name,
        debug=True,
    )
    print("Output folder:", result["run_dir"])
    if not result["ok"]:
        raise SystemExit(result.get("error", "Collection extraction failed."))
    print("HTML:", result["html"])
    print("Documents:", result["documents"])
    print("Images:", OUTPUT_DIR / "images")
    print("Debug previews:", OUTPUT_DIR / "debug")
    print("Logs:", OUTPUT_DIR / "logs")


if __name__ == "__main__":
    main()
