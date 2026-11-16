# Custom_PDF_DRHP_Parser — Geometry-Based Extraction

**Architecture note**  
**Status:** Functional prototype. The first 50 physical pages of each PDF in `input/` have been extracted with Camelot Lattice.  
**Scope:** This prototype performs geometry-based extraction directly from machine-readable PDFs using PyMuPDF and Camelot Lattice. It retains native words, spatial coordinates, detected table structures, merged-cell relationships, and raster image references. A navigable HTML output is generated for viewing and inspecting the extracted content. Structure-aware chunking, hybrid retrieval, and financial question answering are potential downstream developments and are not part of the current implementation.

## Requirements

`requirements.txt` pins `pymupdf`, `camelot-py`, and `pillow`; Camelot installs NumPy, Pandas, OpenCV, pypdfium2, and playa-pdf.

Lattice uses the pdfium backend, so Ghostscript is not required. Hybrid and Network are implemented in the selection code but are not run; `CAMELOT_FLAVORS` is `("lattice",)`.

Install the declared packages with `python -m pip install -r requirements.txt`. Then extract every PDF directly in `input/`:

```text
python main.py
```

`main.py` reads `ACTIVE_PDFS`. `None` means every PDF in `input/`. A filename list runs only those files and leaves the others untouched. One selected PDF is written as `output/<file name>.html`. Several are written as `output/drhp_collection.html`, with one tab per PDF.

Or point the single-PDF script at another file:

```text
python table_structure_ab.py --pdf input/report.pdf --out output
```

## 1. Design Overview

The parser first identifies the TOC region, because **the beginning of the TOC is the boundary of Introductory Information**. That detection only labels sections. Extraction continues through the pages after the TOC and places them under **Main Document**. During this testing phase the page loop stops at physical page 50; deleting that one limit runs the whole PDF. If no TOC is found, every selected page is still extracted under Main Document.

Within those pages, Camelot Lattice proposes table and cell geometry from visible lines. PyMuPDF provides the original words, coordinates, and raster images. Python matches words to cells geometrically; unclaimed words remain ordinary text.

### Structure Obtained Directly from the PDF

The parser extracts information directly from machine-readable PDF files using PyMuPDF and Camelot Lattice.

Rather than treating each PDF page solely as a continuous stream of text, the extraction process retains information about the physical organization of the content.

The current implementation captures:

- **Page-level information:** Physical PDF page references and page coordinates.
- **Native text:** Original PDF words, their bounding boxes, and their source text-block and line identifiers.
- **Table structure:** Detected table boundaries, constituent cells, rows, columns, and merged-cell relationships.
- **Text-to-cell relationships:** Geometric assignment of original PDF words to their corresponding table cells.
- **Images:** Raster image occurrences and their page coordinates.
- **Broad document labels:** Introductory Information, Table of Contents, and Main Document, where identifiable through the current TOC detection logic.

These relationships are constructed from information extracted directly from the source PDF. They do not depend on converting the PDF into HTML first.

The current parser maintains this information through Python objects and extraction logic. The HTML output is generated primarily for human inspection and debugging; it is not intended to be the mandatory input representation for downstream retrieval.

The implementation does not yet establish a complete semantic document hierarchy, automatically classify every paragraph or heading, or export a dedicated structured retrieval index. These remain potential subsequent developments.

This distinction provides the foundation for exploring structure-aware chunking and retrieval while retaining references to the original PDF content.

### Motivation

Although the Sagility India Draft Red Herring Prospectus (DRHP) has been used as the reference document for development and initial testing, the parser has been designed with **document-agnostic table recognition and generalizability** in mind, rather than relying on document-specific structures or hardcoded layouts.

The objective is to develop a geometry-based extraction approach applicable to different machine-readable PDFs. The current run extracts the first 50 physical pages of every PDF in `input/`, using Lattice only. The remainder of each PDF, and tables that have no visible ruling lines, are not covered by this run. Scanned or image-only PDFs would require an additional OCR stage, which is not part of the present implementation.

Preserving the spatial relationship between a word and its cell, between the cells of a table, and between an image and its page may support more reliable downstream retrieval than text that has been flattened into one stream. That retrieval use is not part of this prototype. It depends on structure the extraction already keeps.

### Intuition

The fundamental intuition is that **both table cells and the words contained within them occupy identifiable rectangular regions on a PDF page**.

Camelot identifies the boundaries of tables and their constituent cells using geometric information, while PyMuPDF extracts the original words along with their bounding-box coordinates. Once these coordinates are brought into a common coordinate system, each cell and word can be represented as a rectangle.

The area of intersection between a word's rectangle and a cell's rectangle is then used to determine the word's association with that cell. In the current implementation, **at least 50% of the word's bounding-box area must overlap a cell** for the word to be assigned to it. The cell with the greatest eligible overlap receives the word. Additional checks identify words crossing cell boundaries and reject potentially unreliable table structures.

The central idea is therefore to **reconstruct the physical table structure first and populate its cells using spatial overlap**, without requiring semantic interpretation of the extracted text.

**Geometric intuition (schematic):** The boxes below represent a cell and one word in the same page coordinate system. Their borders cross where the two rectangles overlap. The calculation uses the *word's area* as the denominator, not the cell's area. The illustrated word rectangles lie entirely inside the cell's vertical span, so the shown width fractions also equal their area fractions.

```text
CASE A  -  SUFFICIENT OVERLAP
        +-----------------------------------+
        |   CELL rectangle (Camelot)        |
        |                                   |
        |             +---------------------+-------+
        |             | WORD (PyMuPDF)      |       |
        |             |                     |       |
        |             +---------------------+-------+
        |                                   |
        +-----------------------------------+

Cell/word intersection = 22/30 = ~73% of word area  ->  ASSIGN

CASE B  -  INSUFFICIENT OVERLAP
        +-----------------------------------+
        |   CELL rectangle (Camelot)        |
        |                                   |
        |                             +-----+-----------------------+
        |                             |     | WORD (PyMuPDF)        |
        |                             |     |                       |
        |                             +-----+-----------------------+
        |                                   |
        +-----------------------------------+

Cell/word intersection = 6/30 = 20% of word area   ->  KEEP AS BODY TEXT
```

For each potential assignment, the implementation computes:

```text
word_overlap = area(word_box intersect cell_box) / area(word_box)

word_overlap >= 0.50  ->  eligible for assignment to the best cell
word_overlap <  0.50  ->  remains ordinary text if no other cell qualifies
```

A word whose overlap with a *second* cell is at least 25% is flagged as boundary-crossing. A proposed table is rejected when more than 5% of the native words considered inside its table bounds are boundary-crossing. The parser first checks whether a word's center lies inside the candidate table's bounding rectangle. These are conservative geometric heuristics, not guarantees of correct cell bucketing.

### Architectural responsibilities

```text
+---------------------------+--------------------------------------------------+------------------+-------------------------------------+
| Layer                     | Responsibility                                   | Technology       | Status                              |
+===========================+==================================================+==================+=====================================+
| TOC heading detection     | Recognizes conventional and alternative TOC      | PyMuPDF + Python | Implemented; broader layout         |
|                           | headings, including Hindi variants,              |                  | validation pending                  |
|                           | letter-spaced headings, and letters extracted    |                  |                                     |
|                           | across separate PDF text blocks                  |                  |                                     |
+---------------------------+--------------------------------------------------+------------------+-------------------------------------+
| TOC structural detection  | Identifies repeated title-page-reference pairs   | PyMuPDF + Python | Implemented; broader layout         |
|                           | using Arabic/Roman numerals, textual leaders,    |                  | validation pending                  |
|                           | PDF vector lines, and geometric alignment        |                  |                                     |
|                           | without connectors                               |                  |                                     |
+---------------------------+--------------------------------------------------+------------------+-------------------------------------+
| TOC region formation      | Take the union of detected pages, then include   | Python           | Observed in the POC; gap cases      |
|                           | every page from the earliest to the latest       |                  | still untested                      |
+---------------------------+--------------------------------------------------+------------------+-------------------------------------+
| Section boundary          | Classify content before the TOC starting         | PyMuPDF + Python | Observed in the POC                 |
|                           | position as Introductory Information             |                  |                                     |
+---------------------------+--------------------------------------------------+------------------+-------------------------------------+
| Page normalization        | Correct crop and rotation in a copy, rejecting   | PyMuPDF          | Implemented                         |
|                           | changes to native characters                     |                  |                                     |
+---------------------------+--------------------------------------------------+------------------+-------------------------------------+
| Table geometry            | Lattice only: visible lines and cell borders     | Camelot          | Implemented; other flavors idle     |
|                           | on the selected physical pages                   |                  |                                     |
+---------------------------+--------------------------------------------------+------------------+-------------------------------------+
| Table selection           | Keep Lattice grids that do not overlap           | Python           | Implemented                         |
|                           | another kept grid, and reject a grid when more   |                  |                                     |
|                           | than 5% of its native words are boundary-cut     |                  |                                     |
+---------------------------+--------------------------------------------------+------------------+-------------------------------------+
| Text and images           | Assign PyMuPDF words to accepted cells; retain   | PyMuPDF + Python | Implemented                         |
|                           | unclaimed text and save raster images. Vector    |                  |                                     |
|                           | drawings are inspected for TOC detection but are |                  |                                     |
|                           | not exported as images                           |                  |                                     |
+---------------------------+--------------------------------------------------+------------------+-------------------------------------+
| In-memory content         | Retain words, coordinates, tables, cells, and    | Python           | Implemented in memory; no export    |
| representation            | images for the duration of the extraction        |                  |                                     |
+---------------------------+--------------------------------------------------+------------------+-------------------------------------+
| Publication               | Publish one HTML review only after every         | Python           | Implemented                         |
|                           | selected PDF succeeds. Images, debug, and logs   |                  |                                     |
|                           | stay in per-PDF folders. Logs keep connectors    |                  |                                     |
+---------------------------+--------------------------------------------------+------------------+-------------------------------------+
| Main Document             | Extract pages after the TOC through page 50      | PyMuPDF + Python | Implemented; full PDF not yet       |
|                           | No TOC: still extract the selected pages         |                  |                                     |
+---------------------------+--------------------------------------------------+------------------+-------------------------------------+
```

The extractor can be run directly through `table_structure_ab.py`.

## 2. TOC Detection — Current Prototype

TOC classification is optional, heuristic, and not essential to extracting the underlying content. Heading detection and structural detection can miss a contents list or mark the wrong pages. When they find nothing, the selected pages are still extracted and labeled Main Document. Words, tables, cells, and images come from the PDF geometry either way.

Initial tests across multiple DRHPs revealed incorrect TOC boundaries in some documents. Further validation is required before section classifications can be considered reliable.

### Step 1: Inspect the first 20 physical PDF pages

The maximum detection window is **20 physical pages**, or the full PDF if it contains fewer than 20 pages. These are PDF page positions, not printed page numbers. The 20-page limit is a design choice, not a guarantee that no TOC exists later.

### Step 2: Run two independent detectors

**A. Heading Detection (`H`)**

The parser searches for standard and alternative Table of Contents headings using case-insensitive regular expressions.

Recognized headings include:

- Conventional: `Table of Contents`, `Contents`, `Content`, `TOC`, `Index`, `List of Contents`, `Index of Contents`.
- Alternative: `Inside This Report`, `In This Report`, `What's Inside`, `Navigating This Report`, `Report Navigation`, and related implemented variants.
- Hindi: `विषय सूची`, `अनुक्रमणिका`, and `सूची पत्र`.

The detector also normalizes letter-spaced headings such as `C O N T E N T S` and attempts to reconstruct heading letters extracted into separate PDF text blocks on the same visual line.

Primary headings are full-line matches. Secondary headings may contain a recognized heading phrase with up to two additional words. Both contribute to the same heading-detected page set.

Normalization is used only for detection; original source words remain unchanged.

This reflects `classify_heading_line()`, `_cross_block_heading_rows()`, and `detect_toc_headings()`.

**B. Structural Detection (`S`)**

A reader can usually recognize a Table of Contents page without a heading that says "Contents." The page is a repeated list: a section title sits on the left, a page reference sits on the right, and the space between them joins the two into one entry. That space may hold a leader or a drawn line, or it may simply be open. The page feels like a contents list, rather than an ordinary paragraph, because the same pairing returns down the page and the references share one right-hand edge. Other, less usual layouts can appear, but substantially this is the pattern. Structural detection looks for that repeated relationship.

Supported reference formats

Arabic page references (`1`, `14`, `250`) and valid Roman numeral references (`i`, `iv`, `xii`), including certain surrounding punctuation.

Supported connector patterns

```text
+-------------------------+------------------------------------------------------------------+
| Type                    | Example                                                          |
+=========================+==================================================================+
| Dotted leader           | Risk Factors ........ 32                                         |
+-------------------------+------------------------------------------------------------------+
| Dashed leader           | Risk Factors -------- 32                                         |
+-------------------------+------------------------------------------------------------------+
| Underscore leader       | Risk Factors ________ 32                                         |
+-------------------------+------------------------------------------------------------------+
| Ellipsis / middle dots  | Risk Factors … 32                                                |
+-------------------------+------------------------------------------------------------------+
| Vector-drawn line       | PDF horizontal drawing between the title and the page reference  |
+-------------------------+------------------------------------------------------------------+
| No connector            | Risk Factors followed by a wide gap, then 32                     |
+-------------------------+------------------------------------------------------------------+
```

The detector uses `page.get_drawings()` to inspect horizontal PDF line segments and thin rectangles as possible connectors.

Titles and page references may appear on the same extracted text line or in separate PDF text blocks.

Structural qualification

- A candidate must contain a section-title component and a page reference.
- A geometric gap of at least 4 PDF points can support title–reference association without a connector.
- At least two candidate entries must have page-reference right edges aligned within 2 PDF points.
- Vector lines are supporting evidence, not sufficient by themselves.
- Connector types are retained for diagnostic reporting.

The current implementation does **not explicitly validate page-number progression or indentation hierarchy**. These should not be described as implemented detection rules.

This maps to `_has_leader()`, `detect_toc_vector_lines()`, `_vector_connects()`, `_page_number_kind()`, and `detect_toc_structure()`.

### Step 3: Combine detections by union, not intersection

If heading detection identifies pages `H` and structural detection identifies pages `S`, collect **every page found by either method**:

```text
D = H union S
```

A page is **not discarded** merely because the other detector did not recognize it. If `D` is empty, extraction does not stop. Every selected page is labeled Main Document.

### Step 4: Merge detections into one consecutive candidate region

```text
TOC start page = earliest page in D
TOC end page   = latest page in D
TOC region     = every page from start through end, inclusive
```

Pages between the earliest and latest detections are included **even if neither detector matched them**.

**Example:**

```text
Heading pages:     {3, 5}
Structural pages:  {4, 6, 8}
Combined pages:    {3, 4, 5, 6, 8}

Introductory Information: pages 1-2
Table of Contents:       pages 3-8 (including unmatched page 7)
Main Document:           pages 9 through the testing limit (page 50)
```

This is a deliberate **high-recall candidate-grouping rule**. A false detection can enlarge the resulting region, so the detection logic still requires tests against known TOC boundaries.

### Step 5: Identify the Introductory Information boundary

Everything before the **earliest TOC starting position** is Introductory Information. If the TOC heading begins partway down its starting page, preserve the text preceding that position as introductory content where its position can be established reliably.

The detected consecutive region is labeled **Table of Contents**. Pages after its end are labeled **Main Document**. The current testing limit extracts physical pages 1–50, so a TOC that ends after page 50 would leave no Main Document pages in this run. Pages beyond 50 are not extracted until that limit is removed.

## 3. Extraction Flow

```text
              PDF PARSER: GEOMETRY-BASED DOCUMENT EXTRACTION

  +-----------------------------+
  |          Input PDF          |
  +--------------+--------------+
                 |
                 v
  +-----------------------------+
  | First 20 physical PDF pages |
  +--------------+--------------+
                 |
           +-----+-------------------+
           |                         |
           v                         v
  +------------------+      +------------------+
  | Heading          |      | Structural       |
  | detection (H)    |      | detection (S)    |
  |                  |      |                  |
  | Standard and     |      | Arabic / Roman   |
  | alternative      |      | page references  |
  | headings         |      |                  |
  |                  |      | Textual and      |
  | Spaced-letter    |      | vector           |
  | normalization    |      | connectors       |
  |                  |      |                  |
  | Cross-block      |      | No-connector     |
  | heading          |      | alignment        |
  | reconstruction   |      |                  |
  +--------+---------+      +--------+---------+
           |                         |
           +----------+--------------+
                      |
                      v
  +--------------------------------------+
  | Union H and S; min and max pages     |
  | Include every intervening page      |
  +-------------------+------------------+
                      |
                      v
  +--------------------------------------+
  | Split at TOC starting position       |
  | Introductory Information / TOC       |
  +-------------------+------------------+
                      |
                      v
  +--------------------------------------+
  | Normalize included pages (PyMuPDF)   |
  +-------------------+------------------+
                      |
                      v
  +--------------------------------------+
  | Camelot: Lattice only                |
  | PyMuPDF: native words and images     |
  +-------------------+------------------+
                      |
                      v
  +--------------------------------------+
  | Choose table grids; match word boxes |
  | Preserve unclaimed narrative text    |
  +-------------------+------------------+
                      |
                      v
  +--------------------------------------+
  | HTML: Intro, TOC, Main Document      |
  | and diagnostic run_log.txt           |
  +--------------------------------------+
```

The diagram above is the operational path that publishes the HTML review. The same extraction also holds an in-memory content structure. HTML viewing and a future structured-data interface are separate uses of that structure. The structured-data interface is not implemented.

```text
                ORIGINAL PDF
                     |
                     v
        PyMuPDF + Camelot Lattice
                     |
                     v
        Geometry-Based Extraction
                     |
          +----------+----------+
          |          |          |
       Words &     Tables &    Image
      Coordinates   Cells     References
          |          |          |
          +----------+----------+
                     |
                     v
       In-Memory Content Structure
                     |
          +----------+-----------+
          |                      |
          v                      v
      HTML Viewer         Future Structured
     (Implemented)         Data Interface
                           (Not implemented)
                                  |
                                  v
                         Structure-Aware
                            Chunking
                                  |
                                  v
                         Retrieval System
```

### Camelot–PyMuPDF cell and text mapping

```text
                                       +-------------------------------------+
                                       |         Normalized PDF page         |
                                       +------------------+------------------+
                                                          |
                            +-----------------------------+-----------------------------+
                            |                                                           |
                            v                                                           v
    +-----------------------------------------------+           +-----------------------------------------------+
    |      Camelot: Lattice only                    |           |        PyMuPDF: native PDF extraction         |
    |           Table and cell boundaries           |           |        Original words + bounding boxes        |
    |               Cell-border flags               |           |            Raster image positions             |
    +-----------------------+-----------------------+           +-----------------------+-----------------------+
                            |                                                           |
                            |                                                           |
                            v                                                           |
    +-----------------------------------------------+                                   |
    |          Reconstruct physical cells           |                                   |
    |          and merged row/column spans          |                                   |
    |                                               |                                   |
    +-----------------------+-----------------------+                                   |
                            |                                                           |
                            |                                                           |
                            v                                                           |
    +-----------------------------------------------+                                   |
    |       Convert Camelot cell coordinates        |                                   |
    |         into PyMuPDF page coordinates         |                                   |
    |                                               |                                   |
    +-----------------------+-----------------------+                                   |
                            |                                                           |
                            |                                                           |
                            |                                                           |
                            +-----------------------------+-----------------------------+
                                                          |
                                                          v
                               +-----------------------------------------------------+
                               |        Match native words to physical cells         |
                               |         >=50% overlap: assign to best cell          |
                               |    >=25% secondary overlap: boundary-cut warning    |
                               |                                                     |
                               +--------------------------+--------------------------+
                                                          |
                                                          v
                               +-----------------------------------------------------+
                               |        Reject candidates with >5% cut words         |
                               |         Select non-overlapping table grids          |
                               |        Current run: Lattice grids only              |
                               |                                                     |
                               +--------------------------+--------------------------+
                                                          |
                            +-----------------------------+-----------------------------+
                            |                                                           |
                            v                                                           v
    +-----------------------------------------------+           +-----------------------------------------------+
    |          Accepted tables: HTML cells          |           |          Unclaimed words and images           |
    |         Preserve rowspan and colspan          |           |       Remain ordinary document content        |
    |                                               |           |                                               |
    +-----------------------+-----------------------+           +-----------------------+-----------------------+
                            |                                                           |
                            |                                                           |
                            |                                                           |
                            +-----------------------------+-----------------------------+
                                                          |
                                                          v
                               +-----------------------------------------------------+
                               |                     HTML output                     |
                               |   Intro, TOC, and Main Document, pages 1-50         |
                               |                                                     |
                               +-----------------------------------------------------+
```

Camelot supplies **cell geometry, not the exported cell text**. The parser reconstructs merged cells from Camelot's border flags, aligns their physical boundaries, and converts Camelot's PDF coordinates to the page coordinates used by PyMuPDF. Python assigns each original PyMuPDF word to at most one accepted cell based on geometric overlap. Words not assigned to accepted cells remain in the surrounding text; image occurrences are also preserved. The overlap thresholds are safeguards, not proof of perfectly correct cell bucketing.

On each included page, Camelot Lattice proposes grids from visible lines. Hybrid and Network remain in the code but are not called while `CAMELOT_FLAVORS` contains only `lattice`. If those names are put back, overlapping candidates are ranked Lattice, then Hybrid, then Network. PyMuPDF supplies the text placed into accepted cells, while words outside those cells remain in the body. The code preserves merged-cell spans and native-word ownership checks; it does not rely on Camelot's extracted cell text for the HTML output.

The source PDF is not modified. Page normalization operates on a copy and verifies that native characters are preserved. Existing output files are protected if a run fails. Raster images are exported; vector drawings are not exported as images.

### Raster images, references, and output files

The HTML file is a diagnostic and review output. It is generated so a person can inspect the extraction. It is not a required input for later chunking or retrieval. Those steps can use the in-memory structure from which the HTML is drawn. A separate reusable export of that structure is not implemented.

The parser **extracts embedded raster images from the selected PDF pages**, rather than downloading images from an external source. PyMuPDF reads image-bearing blocks from `page.get_text("dict")`; each occurrence has its own page coordinates and image bytes. Very small occurrences (width or height below 8 PDF points) are skipped.

Image occurrences are numbered in page order and saved using their source image extension, for example `image_0001.png` or `image_0002.jpeg`. Each PDF receives its own namespace under `output/images/`. Image locations are handled geometrically too: if at least **70% of an image's bounding-box area** overlaps a selected table cell, its reference is emitted within that HTML cell; otherwise the image is emitted in the surrounding page content. This 70% threshold is a separate image-placement rule, not the 50% word-assignment rule.

The collection HTML refers to these locally saved images through namespaced relative paths such as `images/lic_india_drhp_2022/image_0001.png` and uses numbered `<imageN>` wrappers.

The default `main.py` runs with `debug=True`, producing PDF-specific paths such as `output/debug/lic_india_drhp_2022/page_0001.png`. These are **diagnostic page renderings with detected table outlines (blue) and cell outlines (red)**; they are separate from the raster images extracted from the source PDF. The direct single-PDF command-line script produces them only if `--debug` is passed.

```text
output/
+-- drhp_collection.html        # One tab per input PDF
+-- images/
|   +-- lic_india_drhp_2022/
|   |   +-- image_0001.png
|   +-- zomato_drhp_2021/
|       +-- image_0001.png
+-- debug/
|   +-- lic_india_drhp_2022/
|       +-- page_0001.png
+-- logs/
    +-- lic_india_drhp_2022/
        +-- run_log.txt
```

For the current Sagility extract, **13 raster image occurrences** were saved from the first 50 pages. The parser does not export PDF vector drawings as standalone images, although it inspects horizontal vectors when detecting TOC entries. Image and native-word ownership checks guard against missing or duplicated *occurrences* in the generated output. A staging folder is used while the run is in progress. The HTML, images, debug pages, and logs are published only after every selected PDF succeeds. One failed PDF leaves the previous package in place.

## 4. Observed run — first 50 pages, Lattice only

The Sagility log from the current collection run reports:

```text
+------------------------------------+--------------------------------------------------------+
| Check                              | Observed result                                        |
+====================================+========================================================+
| TOC search window                  | Physical PDF pages 1-20                                |
+------------------------------------+--------------------------------------------------------+
| Heading detections (H)             | {4}                                                    |
+------------------------------------+--------------------------------------------------------+
| Structural detections (S)          | {4}                                                    |
+------------------------------------+--------------------------------------------------------+
| Combined detections (D)            | {4}                                                    |
+------------------------------------+--------------------------------------------------------+
| TOC start / end                    | Page 4 / Page 4                                        |
+------------------------------------+--------------------------------------------------------+
| TOC starting vertical coordinate   | y = 84.0 on Page 4                                     |
+------------------------------------+--------------------------------------------------------+
| Introductory Information           | Pages 1-3                                              |
+------------------------------------+--------------------------------------------------------+
| Table of Contents                  | Page 4                                                 |
+------------------------------------+--------------------------------------------------------+
| Main Document                      | Pages 5-50                                             |
+------------------------------------+--------------------------------------------------------+
| Extraction range                   | Physical pages 1-50; the rest of the PDF is not run    |
+------------------------------------+--------------------------------------------------------+
| Camelot tables emitted             | 48 Lattice tables on physical pages 1-50               |
+------------------------------------+--------------------------------------------------------+
| TOC structural connector evidence  | 42 text-leader entries                                 |
+------------------------------------+--------------------------------------------------------+
| Raster image occurrences           | 13                                                     |
+------------------------------------+--------------------------------------------------------+
| TOC page words                     | 267 original words, all retained as ordinary text      |
+------------------------------------+--------------------------------------------------------+
| Renderer fallback pages            | None                                                   |
+------------------------------------+--------------------------------------------------------+
| Final run status                   | SUCCESS                                                |
+------------------------------------+--------------------------------------------------------+
```

**Current implementation:** TOC heading and structural detection support alternative heading vocabulary, letter-spaced headings, cross-block heading letters, Arabic and Roman page references, dashed and underscore leaders, PDF vector-drawn connectors, and aligned title–reference pairs without connectors. The latest Sagility run reports 42 text-leader structural evidence entries. Other layouts are implemented but have not all been validated on a broad collection of real-world filings.

The HTML labels pages 1-3 as Introductory Information, page 4 as Table of Contents, and pages 5-50 as Main Document. The same Lattice, 50-page run also produced the other documents now in `output/drhp_collection.html`:

```text
+------------------------------------------+--------+
| Document                                 | Tables |
+==========================================+========+
| LIC India DRHP 2022                      | 41     |
+------------------------------------------+--------+
| Ola Electric Mobility DRHP 2023          | 45     |
+------------------------------------------+--------+
| Paytm One97 Communications DRHP 2021     | 3      |
+------------------------------------------+--------+
| Sagility India DRHP                      | 48     |
+------------------------------------------+--------+
| Swiggy Updated DRHP 2024                 | 55     |
+------------------------------------------+--------+
| Zomato DRHP 2021                         | 42     |
+------------------------------------------+--------+
```

**What this establishes:** Heading and structural detection both identified Sagility's TOC on Page 4, and the output continues into Main Document through page 50. The six PDFs above completed with Lattice only. Word-accounting checks passed for the processed pages.

**What this does not establish:** These are the first 50 pages, not the full filings. Paytm's three tables show that borderless regions stay as ordinary text under Lattice. The logs do not prove cell-level accuracy, and they do not yet cover gaps, conflicting detector results, or a TOC that begins after the 20-page search window.

## 5. Validation and Next Steps

Before treating the revised TOC detector as universally reliable, test documents with:

- Standard, alternative, and Hindi TOC headings, including documents with no standard heading.
- Letter-spaced headings within one text line and across separate PDF blocks.
- Dots, dashes, underscores, and ellipses.
- Continuous horizontal vector lines.
- No-connector layouts with aligned page numbers.
- Arabic and Roman numeral page references.
- Titles and page references extracted into separate blocks.
- TOCs beginning after page 10 but within the 20-page search window.
- Multi-page TOCs, intermediate gaps, and false positives caused by financial tables.
- TOC headings appearing midway down a page.
- Files with no detectable TOC.

For each document, compare the detected start and end against manually established TOC boundaries. Only adopt TOC-detection changes when they preserve successful baseline cases and add correct detections without introducing false boundaries.

Every later change to the extraction has the same no-regression requirement. Compare it with the current baseline before it replaces that baseline. Acceptance requires preserving text that is already extracted correctly, image occurrences, accepted table structures, merged cells, and word-to-cell relationships, and showing an improvement in at least one substantive part of the extraction. A change that loses accuracy, completeness, or structural fidelity does not replace the baseline.

**Alternative evaluated (limited test):** Microsoft's pretrained Table Transformer ([Detection](https://huggingface.co/microsoft/table-transformer-detection), [Structure Recognition All](https://huggingface.co/microsoft/table-transformer-structure-recognition-v1.1-all), and [Structure Recognition Fin](https://huggingface.co/microsoft/table-transformer-structure-recognition-v1.1-fin)) was tried on Sagility DRHP pages 1–4, but in this preliminary evaluation it did not reliably detect and reconstruct the relevant tables. The custom Camelot–PyMuPDF pipeline was therefore retained; this is not a general benchmark of the Microsoft models.

## 6. Future Direction — Structure-Aware Chunking and Retrieval

The current parser establishes a geometry-based content representation directly from the source PDF, preserving native text, spatial coordinates, accepted table structures, merged-cell relationships, and image references.

A possible future direction is to use this information for document chunking and retrieval, rather than relying exclusively on fixed-size chunks created from flattened PDF text.

### 6.1. Structured Data Interface

The existing in-memory extraction representation could be exposed through a reusable structured-data interface.

This interface may retain document identifiers, physical PDF page references, text elements, table and cell identifiers, geometric coordinates, and references to the original source content.

The HTML viewer would remain an optional inspection output, rather than becoming a required intermediary for retrieval.

### 6.2. Structure-Aware Chunking

Chunk creation could consider the available content structure, including text blocks, tables, and their associations.

Possible approaches include:

- Grouping adjacent narrative content into meaningful chunks, subject to reliable reading-order reconstruction.
- Preserving complete tables where practical.
- Dividing large tables into smaller retrieval units while retaining relevant column headers, row identifiers, merged-cell relationships, and units.
- Maintaining links between smaller retrieval chunks and their larger source context.
- Preserving document and physical page references for evidence verification.

The intention is to reduce the loss of contextual relationships that can occur when documents are divided using token counts alone.

### 6.3. Hybrid Retrieval

The resulting chunks could be indexed through two complementary retrieval methods:

**BM25-based lexical retrieval** for identifying passages and tables containing relevant terminology, financial labels, and exact expressions.

**Embedding-based semantic retrieval** for identifying conceptually relevant content even when the query and the document use different terminology.

Both indexes could refer to the same underlying chunk identifiers and source metadata.

### 6.4. Retrieval Fusion and Reranking

Results from lexical and semantic retrieval could be combined using Reciprocal Rank Fusion (RRF).

A subsequent reranking stage, potentially using a pretrained cross-encoder, could refine the relevance ordering of the retrieved candidates.

These components remain proposed options and should be evaluated against simpler retrieval baselines before adoption.

### 6.5. Context Recovery and Agent-Based Verification

A retrieval result would retain references to its original document context.

For example, a retrieved financial table row could provide access to the complete table, its column headers, the surrounding narrative, and the corresponding physical PDF page.

An exploratory agent could use this information to inspect additional evidence when the initial retrieval is insufficient or ambiguous.

For financial questions, potential verification steps include checking the relevant financial year, metric definition, reporting entity, units, and table row–column association.

Answer generation would use the retrieved evidence, with source references retained wherever possible.

### 6.6. Implementation Status

The geometry-based PDF extraction is implemented as a functional prototype.

The reusable structured-data interface, structure-aware chunking, BM25 and embedding indexes, fusion, reranking, and agent-based evidence verification are not implemented as part of this parser.

They represent a possible downstream retrieval architecture to be explored and validated separately.

## 7. Expected Benefits, Evaluation, and Further Improvements

### 7.1. Potential Benefits

Retaining geometric and structural relationships during extraction creates opportunities for more context-preserving retrieval.

Potential advantages include:

- **Improved chunk coherence:** Reduced separation of logically related content.
- **Table-context preservation:** Maintaining the association between financial values, row labels, column headers, and units.
- **Targeted retrieval:** Enabling retrieval of individual content units while retaining access to their broader source context.
- **Evidence traceability:** Maintaining references to the original PDF document and physical page.
- **Flexible document navigation:** Allowing section information to assist retrieval when available without making predefined section names mandatory.

These are expected benefits rather than demonstrated performance gains. Their effect on retrieval accuracy must be measured.

### 7.2. Extraction Improvements Under Investigation

Possible future extraction improvements include:

- Reliable reading-order reconstruction for multi-column layouts.
- Identification of landscape pages and two-page spreads.
- Improved TOC boundary detection and classification.
- Header and footer identification using available native PDF structural information.
- Further evaluation of table-detection coverage across different document layouts.
- Preservation of relevant context surrounding tables and other content elements.

These improvements must not compromise the accuracy or completeness of the existing extraction.

### 7.3. No-Regression Requirement

Every proposed modification should be compared against the current baseline.

Acceptance requires preserving existing correct text extraction, image occurrences, table structures, merged cells, and word-to-cell relationships while demonstrating an improvement in at least one substantive dimension.

Changes that introduce meaningful losses in accuracy, completeness, or structural fidelity should not replace the baseline.

### 7.4. Retrieval Evaluation

Once the retrieval pipeline is implemented, it should be compared against a conventional flattened-text chunking baseline using the same documents and evaluation questions.

Relevant measurements may include:

- Retrieval Recall@K and ranking quality.
- Correct identification of the relevant source page or table.
- Accuracy of financial values and their associated years and units.
- End-to-end answer accuracy.
- Completeness and correctness of retrieved evidence.

The objective is to determine whether the extracted document structure provides measurable improvements in retrieval and question answering.
