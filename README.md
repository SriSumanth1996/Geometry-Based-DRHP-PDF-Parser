# Geometry-Based DRHP PDF Parser

Geometry-based extraction for machine-readable Draft Red Herring Prospectuses of Indian companies. PyMuPDF supplies the original words, coordinates, and raster images. Camelot Lattice supplies table and cell geometry from visible lines. Python assigns words to cells by overlap.

The HTML file is a review of that extraction. Table of Contents detection only labels Introductory Information, Table of Contents, and Main Document. Chunking and retrieval are not part of this prototype.

The design is written up in `Custom_Geometry_Based_DRHP_PDF_Extraction_Architecture.md`.

## Run

Python 3.12 or newer.

```text
python -m pip install -r requirements.txt
python main.py
```

Place PDFs directly in `input/`. `ACTIVE_PDFS = None` in `main.py` runs every PDF there. A filename list runs only those files.

One PDF is written as `output/<file name>.html`. Several are written as `output/drhp_collection.html`. Images, debug page pictures, and logs are stored under `output/` in a folder per PDF.

The current code stops at physical page 50. That limit is one line in `table_structure_ab.py`. Deleting it runs the whole PDF.

`input/` in this repository holds six DRHPs of Indian companies. `output/drhp_collection.html` is the review of their first 50 pages.
