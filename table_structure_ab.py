"""Extract every physical page. TOC detection only labels the sections.

Introductory Information and Table of Contents use the same split as before.
Pages after the detected TOC are Main Document. If no TOC is found, every
page is still extracted under Main Document.

The table, word, image, and publication logic is unchanged.

Run from this folder: python main.py
Or: python table_structure_ab.py --pdf input/report.pdf --out output
Dependencies: pymupdf, camelot-py
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from collections import Counter, defaultdict
from html import escape
from importlib import metadata
from pathlib import Path
import math
import re
import secrets
import shutil
import pymupdf

HERE = Path(__file__).resolve().parent

# Heading detection searches only the opening pages. Primary and secondary
# patterns are one method: both add the page to the same heading-page set.
MAX_TOC_SEARCH_PAGES = 20
_TOC_HEADING_CORE = (
    r"table[ \t]*of[ \t]*contents?|"
    r"list[ \t]*of[ \t]*contents?|"
    r"index[ \t]*of[ \t]*contents?|"
    r"contents?[ \t]+(?:of[ \t]+(?:the[ \t]+)?report|at[ \t]+a[ \t]+glance)|"
    r"(?:in|inside)[ \t]+(?:this|the)[ \t]+(?:annual[ \t]+)?report|"
    r"what['’]?s[ \t]+inside|"
    r"navigating[ \t]+this[ \t]+report|"
    r"report[ \t]+navigation|"
    r"\bcontents?\b|"
    r"\btoc\b|"
    r"\bindex\b|"
    r"विषय[\s\-]*सूची|"
    r"अनुक्रमणिका|"
    r"सूची[\s\-]*पत्र"
)
PRIMARY_TOC_HEADING = re.compile(
    rf"^[ \t]*(?:{_TOC_HEADING_CORE})[ \t]*[:.\-–—]*[ \t]*$",
    re.I,
)
_TOC_HEADING_SEARCH = re.compile(_TOC_HEADING_CORE, re.I)

# ---------------------------------------------------------------------------
# Table Detection, Word Assignment, and Output Configuration
# ---------------------------------------------------------------------------
#
# CAMELOT_FLAVORS:
#     Methods used by Camelot to identify table structures and cell geometry.
#     Lattice only for now: tables need visible lines and cell borders.
#     Hybrid combines lines with text alignment. Network uses text alignment
#     alone. Put either name back in this tuple to run it again.
CAMELOT_FLAVORS = ("lattice",)

# MAX_CUT_WORD_FRACTION:
#     Maximum acceptable fraction of boundary-crossing words in a proposed
#     table. A word is flagged when its second-highest cell overlap reaches
#     MIN_SECONDARY_CELL_OVERLAP.
#     Reject the table candidate if this fraction exceeds 5%.
MAX_CUT_WORD_FRACTION = 0.05

# MIN_CELL_WORD_OVERLAP:
#     Minimum overlap required between a word's bounding box and its
#     best-matching cell. At least 50% of the word's area must overlap
#     the cell for assignment.
#     Otherwise, the word remains available as ordinary text.
#
#     Literature support:
#     Microsoft's Table Transformer (FinTabNet preprocessing) uses
#     the same word-area overlap principle with a 50% threshold.
#
#     Reference:
#     File: scripts/process_fintabnet.py
#     Function: extract_pdf_text()
#
#     Line 800: Defines the default threshold as 0.5.
#     Line 802 assigns a word when:
#         intersection_area / word_area > threshold
#
#     Thus, Microsoft requires more than 50% of the word's area
#     to overlap the cell. Our implementation uses >= 50%.
#
#     Source (Microsoft GitHub, lines 800-808):
#     https://github.com/microsoft/table-transformer/blob/main/scripts/process_fintabnet.py#L800-L808
MIN_CELL_WORD_OVERLAP = 0.50

# MIN_SECONDARY_CELL_OVERLAP:
#     A word is boundary-crossing when its second-highest cell overlap is
#     at least 25% of its area.
MIN_SECONDARY_CELL_OVERLAP = 0.25

# Note:
#     The 5%, 25%, and 50% thresholds are configurable heuristics, not proven
#     universal optima. Changes should be validated against the baseline.
# ---------------------------------------------------------------------------


class Word:
    """
    Represents a single word extracted from a PDF using PyMuPDF.

    Stores the word's original text, bounding-box coordinates, and
    position within the PDF's text-block and line structure.

    The bounding box is used to geometrically match the word with
    table-cell boundaries detected by Camelot.

    Attributes:
        id: Unique identifier of the word within its page.
        box: PyMuPDF rectangle containing the word (x0, y0, x1, y1).
        text: Original word extracted from the PDF.
        block: Index of the text block containing the word.
        line: Index of the line within that text block.
    """

    def __init__(self, id, box, text, block, line):
        self.id = id
        self.box = box
        self.text = text
        self.block = block
        self.line = line


class Image:
    """
    Represents a raster image extracted from a PDF page using PyMuPDF.

    Stores the image's bounding-box coordinates, unique occurrence number,
    and saved filename. The geometric coordinates allow the extraction
    pipeline to determine whether the image belongs inside a detected
    table cell or should remain part of the surrounding page content.

    Provides HTML for embedding the image in the reconstructed document
    while preserving its reference to the original PDF.

    Attributes:
        box: PyMuPDF rectangle (x0, y0, x1, y1) representing the
             image's position on the PDF page.
        number: Unique sequential identifier for the image occurrence
                within the extracted document.
        filename: Name of the extracted image file stored in the
                  output/images directory.

    Methods:
        html():
            Generates an HTML image element enclosed in numbered
            image tags. Used when the image belongs inside a
            reconstructed HTML table cell. Escapes the filename
            for safe insertion into an HTML attribute.

    Note:
        This class stores image metadata and generates output references.
        Actual image extraction, saving, and geometric assignment are
        performed by other functions in the pipeline.
    """

    def __init__(self, box, number, filename):
        self.box = box
        self.number = number
        self.filename = filename

    def html(self) -> str:
        return (f'<image{self.number}><img src="images/{escape(self.filename, quote=True)}" '
                f'alt="Image {self.number}" loading="lazy"/></image{self.number}>')

class Cell:
    """
    Represents one physical cell in a reconstructed table.

    The rectangle is the cell's position on the PDF page. Row and column
    indexes record where that rectangle sits on the table's shared grid,
    including a span when the cell covers more than one row or column.
    Words and images are attached later by geometric overlap. They are
    not taken from Camelot's own cell text.

    Attributes:
        box: PyMuPDF rectangle (x0, y0, x1, y1) of the cell on the page.
        r0: First row index covered by the cell. Inclusive.
        r1: Row index just after the last row covered by the cell.
        c0: First column index covered by the cell. Inclusive.
        c1: Column index just after the last column covered by the cell.
        words: Native PDF words assigned to this cell.
        images: Raster images placed inside this cell.

    Note:
        A newly built cell starts as a single grid slot. grid_from_boxes()
        replaces r0, r1, c0, and c1 from the cell rectangle. assign_words()
        fills words. Image placement fills images.
    """

    def __init__(self, box, r0=0, r1=1, c0=0, c1=1, words=None, images=None):
        self.box = box
        self.r0 = r0
        self.r1 = r1
        self.c0 = c0
        self.c1 = c1
        self.words = [] if words is None else words
        self.images = [] if images is None else images


class Table:
    """
    Represents one candidate table proposed by Camelot.

    Holds the table rectangle, its physical cells, and the bookkeeping
    produced when native PDF words are matched to those cells. Selection
    keeps or rejects the candidate from these counts. It does not treat
    the counts as a measure of table accuracy.

    Attributes:
        box: PyMuPDF rectangle covering every cell in the table.
        cells: Physical cells, including merged cells.
        engine: Camelot method that proposed the table, such as
                camelot-lattice, camelot-hybrid, or camelot-network.
        reference: Short label used in the run log, such as p1:lattice:1.
        xedges: Vertical grid lines, in PDF points, from left to right.
        yedges: Horizontal grid lines, in PDF points, from top to bottom.
        cut_words: Words inside the table whose second cell also overlaps
                   at least 25% of the word.
        loose_words: Words inside the table that were not assigned to a cell.
        native_count: Words whose center falls inside the table rectangle.

    Methods:
        ids():
            Returns the set of word identifiers assigned to any cell.
            A word is counted once even if the caller inspects every cell.

        cut_fraction():
            Returns cut_words divided by native_count. An empty table
            uses a denominator of 1 so the fraction stays defined.
            select_tables() rejects a candidate when this exceeds 5%.

    Note:
        grid_from_boxes() fills xedges, yedges, and the table rectangle.
        assign_words() fills the word counts and each cell's word list.
    """

    def __init__(self, box, cells, engine, reference, xedges=None, yedges=None,
                 cut_words=0, loose_words=0, native_count=0):
        self.box = box
        self.cells = cells
        self.engine = engine
        self.reference = reference
        self.xedges = [] if xedges is None else xedges
        self.yedges = [] if yedges is None else yedges
        self.cut_words = cut_words
        self.loose_words = loose_words
        self.native_count = native_count

    def ids(self):
        return {w.id for c in self.cells for w in c.words}

    def cut_fraction(self):
        return self.cut_words / max(1, self.native_count)


def area(box: pymupdf.Rect) -> float:
    return max(0.0, box.width) * max(0.0, box.height)


def center(box: pymupdf.Rect) -> pymupdf.Point:
    return pymupdf.Point((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)


def union(boxes) -> pymupdf.Rect:
    boxes = list(boxes)
    if not boxes:
        return pymupdf.Rect()
    result = pymupdf.Rect(boxes[0])
    for box in boxes[1:]:
        result |= box
    return result


def clustered(values, tolerance=0.6) -> list[float]:
    groups = []
    for value in sorted(values):
        if groups and value - groups[-1][0] <= tolerance:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [sum(group) / len(group) for group in groups]


def index_nearest(edges, value):
    return min(range(len(edges)), key=lambda i: abs(edges[i] - value))


def grid_from_boxes(table: Table) -> None:
    """Keep real cell rectangles, not a padded DataFrame of text values.

    Several logical grids can share one fine master grid. colspan/rowspan
    represent that correctly; an empty string is NOT evidence of a merge.
    """
    unique = {}
    for cell in table.cells:
        if cell.box.is_empty or not all(math.isfinite(x) for x in cell.box):
            continue
        unique.setdefault(tuple(round(x, 3) for x in cell.box), cell)
    table.cells = list(unique.values())
    if not table.cells:
        raise ValueError("no usable cell boxes")
    table.xedges = clustered(x for c in table.cells for x in (c.box.x0, c.box.x1))
    table.yedges = clustered(y for c in table.cells for y in (c.box.y0, c.box.y1))
    if len(table.xedges) < 2 or len(table.yedges) < 2:
        raise ValueError("collapsed table grid")
    if (len(table.xedges) - 1) * (len(table.yedges) - 1) > 100000:
        raise ValueError("unreasonably large inferred grid")
    occupied = set()
    for cell in table.cells:
        cell.c0 = index_nearest(table.xedges, cell.box.x0)
        cell.c1 = index_nearest(table.xedges, cell.box.x1)
        cell.r0 = index_nearest(table.yedges, cell.box.y0)
        cell.r1 = index_nearest(table.yedges, cell.box.y1)
        if cell.r0 >= cell.r1 or cell.c0 >= cell.c1:
            raise ValueError("cell collapsed while aligning close boundaries")
        slots = {(r, c) for r in range(cell.r0, cell.r1)
                 for c in range(cell.c0, cell.c1)}
        if occupied & slots:
            raise ValueError("conflicting cell geometry; not safe to guess a merge")
        occupied |= slots
    table.box = union(c.box for c in table.cells)


def camelot_cells(raw, page_height: float) -> list[Cell]:
    """Recover rectangular merged cells from Camelot's actual edge flags.

    First join horizontal slots when BOTH sides say the internal separator
    is absent. Then join identical horizontal spans vertically. This avoids
    turning non-rectangular connected areas into invented rectangular cells.
    Text, capitalization, and blank values are not used to infer spans.
    """
    matrix = raw.cells
    if not matrix or not matrix[0]:
        return []
    width = len(matrix[0])
    if any(len(row) != width for row in matrix):
        raise ValueError("Camelot cell matrix is ragged")
    runs = []
    active = {}
    for r, row in enumerate(matrix):
        next_active = {}
        left = 0
        while left < width:
            right = left + 1
            while (right < width and not bool(row[right - 1].right)
                   and not bool(row[right].left)):
                right += 1
            key = (left, right)
            previous = active.get(key)
            can_extend = previous is not None and all(
                not bool(matrix[r - 1][c].bottom) and not bool(matrix[r][c].top)
                for c in range(left, right)
            )
            if can_extend:
                previous[1] = r + 1
                current = previous
            else:
                current = [r, r + 1, left, right]
                runs.append(current)
            next_active[key] = current
            left = right
        active = next_active
    result = []
    for r0, r1, c0, c1 in runs:
        boxes = [pymupdf.Rect(float(matrix[r][c].x1),
                             page_height - float(matrix[r][c].y2),
                             float(matrix[r][c].x2),
                             page_height - float(matrix[r][c].y1))
                 for r in range(r0, r1) for c in range(c0, c1)]
        result.append(Cell(union(boxes)))
    return result


def detect_camelot(path: Path, page_count: int, heights, log):
    import camelot
    results = defaultdict(list)
    successes = 0
    for page_index in range(page_count):
        for flavor in CAMELOT_FLAVORS:
            try:
                # Never split a PDF word just to make it fit a speculative grid.
                # No copy_text: repeated spanning text must not become new text.
                kwargs = {"split_text": False, "suppress_stdout": True}
                if flavor in ("lattice", "hybrid"):
                    kwargs.update(backend="pdfium", copy_text=None, line_scale=40)
                detected = camelot.read_pdf(str(path), pages=str(page_index + 1),
                                            flavor=flavor, **kwargs)
                successes += 1
                log.append(f"PAGE {page_index + 1} Camelot {flavor}: {len(detected)} detections")
                for i, raw in enumerate(detected, 1):
                    ref = f"p{page_index + 1}:{flavor}:{i}"
                    try:
                        cells = camelot_cells(raw, heights[page_index])
                        table = Table(union(c.box for c in cells), cells,
                                      f"camelot-{flavor}", ref)
                        grid_from_boxes(table)
                        results[page_index].append(table)
                    except Exception as exc:
                        log.append(f"REJECT {ref}: {type(exc).__name__}: {exc}")
            except Exception as exc:
                log.append(f"ERROR p{page_index + 1} Camelot {flavor}: {type(exc).__name__}: {exc}")
    if page_count and not successes:
        raise RuntimeError("All Camelot calls failed; see run_log.txt")
    return results


def read_words(page) -> list[Word]:
    return [Word(i, pymupdf.Rect(w[:4]), w[4], int(w[5]), int(w[6]))
            for i, w in enumerate(page.get_text("words", sort=False))]


def assign_words(table: Table, words: list[Word]) -> None:
    """Account for each native word once; preserve words outside/near a grid.

    Coverage here is a bookkeeping invariant, NOT a table-accuracy score.
    A cell boundary cutting a word is an independent warning signal.
    """
    for cell in table.cells:
        cell.words.clear()
    table.cut_words = table.loose_words = table.native_count = 0
    for word in words:
        if not table.box.contains(center(word.box)):
            continue
        table.native_count += 1
        overlaps = [(area(word.box & cell.box) / max(area(word.box), 1e-9), i)
                    for i, cell in enumerate(table.cells)]
        overlaps.sort(reverse=True)
        if not overlaps or overlaps[0][0] < MIN_CELL_WORD_OVERLAP:
            table.loose_words += 1
            continue
        if len(overlaps) > 1 and overlaps[1][0] >= MIN_SECONDARY_CELL_OVERLAP:
            table.cut_words += 1
        table.cells[overlaps[0][1]].words.append(word)


def select_tables(proposed: list[Table], words: list[Word], log) -> list[Table]:
    valid = []
    for table in proposed:
        assign_words(table, words)
        if not table.ids():
            log.append(f"REJECT {table.reference}: no assignable native text (OCR not enabled)")
            continue
        if table.cut_fraction() > MAX_CUT_WORD_FRACTION:
            log.append(f"REJECT {table.reference}: cell boundaries cut {table.cut_words}/"
                       f"{table.native_count} native words; source stays outside tables")
            continue
        valid.append(table)
    # Prefer explicit ruling geometry over a broad text-alignment guess.
    # Use source coverage only within that evidence tier. Otherwise a large
    # speculative grid wins simply by swallowing more text. No reward for
    # many columns, and no penalty for full-width prose or section headings.
    preference = {"camelot-lattice": 3, "camelot-hybrid": 2, "camelot-network": 1}
    valid.sort(key=lambda t: (preference.get(t.engine, 0),
                              -t.cut_fraction(), len(t.ids())), reverse=True)
    selected = []
    for table in valid:
        conflicts = [other for other in selected if table.ids() & other.ids()
                     or area(table.box & other.box) / max(1.0, min(area(table.box), area(other.box))) > 0.03]
        if conflicts:
            other = conflicts[0]
            common = len(table.ids() & other.ids())
            similarity = common / max(1, max(len(table.ids()), len(other.ids())))
            reason = "duplicate spatial detection" if similarity > .90 else "overlapping alternative, not a duplicate"
            log.append(f"SKIP {table.reference}: {reason}; keeping {other.reference}; "
                       "any unclaimed native words stay in body text")
            continue
        selected.append(table)
        spans = sum(c.c1 - c.c0 > 1 or c.r1 - c.r0 > 1 for c in table.cells)
        log.append(f"KEEP {table.reference}: {len(table.cells)} physical cells, {spans} merged cells, "
                   f"{len(table.ids())} assigned words, {table.loose_words} unassigned words, "
                   f"{table.cut_words} boundary-crossing words")
    return sorted(selected, key=lambda t: (t.box.y0, t.box.x0))


def word_lines(words: list[Word]):
    grouped = defaultdict(list)
    for word in words:
        grouped[(word.block, word.line)].append(word)
    lines = []
    for identity, group in grouped.items():
        group.sort(key=lambda w: (w.box.x0, w.id))
        lines.append((union(w.box for w in group), identity, group))
    return sorted(lines, key=lambda event: (event[0].y0, event[0].x0))


def render_cell(cell: Cell) -> str:
    events = []
    for box, _identity, group in word_lines(cell.words):
        text = escape(" ".join(w.text for w in group))
        events.append((box.y0, box.x0, text))
    events.extend((image.box.y0, image.box.x0, image.html()) for image in cell.images)
    return "<br/>".join(event[2] for event in sorted(events, key=lambda e: (e[0], e[1])))


def table_html(table: Table) -> str:
    """Emit real colspan/rowspan and physical track proportions in HTML.

    No inferred semantic headers: capitalization must not turn data into th.
    Empty physical cells stay empty. Slots covered by a span are not emitted.
    """
    nrows, ncols = len(table.yedges) - 1, len(table.xedges) - 1
    starts = {(cell.r0, cell.c0): cell for cell in table.cells}
    occupied = {(r, c) for cell in table.cells for r in range(cell.r0, cell.r1)
                for c in range(cell.c0, cell.c1)}
    parts = ['<table border="1" width="100%" cellspacing="0" cellpadding="6">', '<colgroup>']
    width = max(table.xedges[-1] - table.xedges[0], 1e-9)
    for c in range(ncols):
        percent = 100 * (table.xedges[c + 1] - table.xedges[c]) / width
        parts.append(f'<col width="{percent:.5f}%"/>')
    parts += ['</colgroup>', '<tbody>']
    for r in range(nrows):
        parts.append('<tr>')
        for c in range(ncols):
            cell = starts.get((r, c))
            if cell is not None:
                parts.append(f'<td colspan="{cell.c1 - cell.c0}" rowspan="{cell.r1 - cell.r0}" '
                             f'valign="top">{render_cell(cell)}</td>')
            elif (r, c) not in occupied:
                # Preserve an unexplained geometric hole without inventing text.
                parts.append('<td></td>')
        parts.append('</tr>')
    parts += ['</tbody>', '</table>']
    return "\n".join(parts)


def position_images(tables, images):
    free = []
    for table in tables:
        for cell in table.cells:
            cell.images.clear()
    for image in images:
        options = [(area(image.box & cell.box) / max(area(image.box), 1e-9), cell)
                   for table in tables for cell in table.cells]
        best = max(options, key=lambda item: item[0], default=(0, None))
        if best[0] >= .70:
            best[1].images.append(image)
        else:
            free.append(image)
    return free


def render_page(page, words, tables, images, next_table, log):
    claimed = Counter(w.id for table in tables for cell in table.cells for w in cell.words)
    if any(n != 1 for n in claimed.values()):
        raise ValueError("a native word has more than one table owner")
    outside = [word for word in words if word.id not in claimed]
    free_images = position_images(tables, images)
    events = []
    for table in tables:
        next_table += 1
        block = f"<table{next_table}>\n\n{table_html(table)}\n\n</table{next_table}>"
        events.append((table.box.y0, table.box.x0, "table", block, None))
    for box, identity, group in word_lines(outside):
        events.append((box.y0, box.x0, "text", escape(" ".join(w.text for w in group), quote=False), (identity, box)))
    for image in free_images:
        events.append((image.box.y0, image.box.x0, "image", image.html(), None))
    parts = []
    previous = None
    for y, x, kind, content, info in sorted(events, key=lambda e: (e[0], e[1])):
        separator = "\n\n"
        if kind == "text" and previous and previous[0] == "text":
            prev_identity, prev_box = previous[1]
            identity, box = info
            if identity[0] == prev_identity[0] and box.y0 - prev_box.y1 < max(box.height, 2):
                separator = "\n"
        parts.append((separator if parts else "") + content)
        previous = (kind, info)
    emitted_ids = list(claimed.elements()) + [word.id for word in outside]
    if Counter(emitted_ids) != Counter(word.id for word in words):
        raise ValueError("native-word ownership check failed")
    emitted_images = free_images + [image for table in tables for cell in table.cells for image in cell.images]
    if Counter(i.number for i in emitted_images) != Counter(i.number for i in images):
        raise ValueError("image occurrence ownership check failed")
    log.append(f"PAGE {page.number + 1}: {len(words)} native word IDs accounted for once; "
               f"{len(claimed)} in tables, {len(outside)} in body, {len(images)} image occurrences. "
               "This is NOT proof of correct cell assignment.")
    return "".join(parts).strip(), next_table


def extract_images(doc, folder: Path, log):
    folder.mkdir(parents=True, exist_ok=True)
    results = defaultdict(list)
    number = 0
    for page_index, page in enumerate(doc):
        blocks = [b for b in page.get_text("dict")["blocks"] if b["type"] == 1 and b.get("image")]
        for block in sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0])):
            box = pymupdf.Rect(block["bbox"])
            if box.width < 8 or box.height < 8:
                continue
            number += 1
            suffix = str(block.get("ext", "png")).lower().lstrip(".")
            if not re.fullmatch(r"[a-z0-9]+", suffix):
                raise ValueError(f"Unsafe image extension: {suffix!r}")
            name = f"image_{number:04d}.{suffix}"
            (folder / name).write_bytes(block["image"])
            results[page_index].append(Image(box, number, name))
    log.append(f"Raster image occurrences saved: {number}; vector drawings are not exported as images")
    return results


def _search_limit(doc):
    return min(MAX_TOC_SEARCH_PAGES, doc.page_count)


def _page_lines(page):
    grouped = defaultdict(list)
    for word in read_words(page):
        grouped[(word.block, word.line)].append(word)
    lines = []
    for group in grouped.values():
        group.sort(key=lambda word: (word.box.x0, word.id))
        lines.append(group)
    lines.sort(key=lambda group: (group[0].box.y0, group[0].box.x0))
    return lines


def _line_text(group):
    return " ".join(word.text for word in group)


_SPACED_HEADING_LETTERS = re.compile(
    r"(?<![A-Za-z])(?:[A-Za-z][ \t]+){2,}[A-Za-z](?![A-Za-z])"
)


def _normalize_toc_heading(text):
    """Collapse visually letter-spaced headings without changing source words."""
    return _SPACED_HEADING_LETTERS.sub(
        lambda match: re.sub(r"[ \t]+", "", match.group()), text.strip()
    )


def classify_heading_line(text):
    """Recognize literal and letter-spaced TOC headings."""
    line = _normalize_toc_heading(text)
    if not line:
        return None
    if PRIMARY_TOC_HEADING.fullmatch(line):
        return "primary"
    match = _TOC_HEADING_SEARCH.search(line)
    if not match:
        return None
    remainder = line[:match.start()] + line[match.end():]
    if len(re.findall(r"\w+", remainder, flags=re.UNICODE)) <= 2:
        return "secondary"
    return None


def _cross_block_heading_rows(page):
    """Recover spaced-out letters which PyMuPDF split across text blocks.

    Additional heading candidates only: the normal page lines remain untouched.
    """
    words = read_words(page)
    singles = [word for word in words if re.fullmatch(r"[A-Za-z]", word.text)]
    rows = []
    for word in sorted(singles, key=lambda w: ((w.box.y0 + w.box.y1) / 2, w.box.x0)):
        mid = (word.box.y0 + word.box.y1) / 2
        row = next((row for row in rows if abs(mid - row["mid"]) <= max(2, word.box.height * 0.30)), None)
        if row is None:
            rows.append({"mid": mid, "members": [word]})
        else:
            row["members"].append(word)
            row["mid"] = sum((w.box.y0 + w.box.y1) / 2 for w in row["members"]) / len(row["members"])
    for row in rows:
        if len(row["members"]) < 3:
            continue
        mid = row["mid"]
        height = max(word.box.height for word in row["members"])
        same_visual_line = [
            word for word in words
            if abs((word.box.y0 + word.box.y1) / 2 - mid) <= max(2, height * 0.30)
        ]
        if len(same_visual_line) <= 40:
            yield sorted(same_visual_line, key=lambda w: (w.box.x0, w.id))


def detect_toc_headings(doc, max_pages=None):
    """Find TOC headings, including visually spaced letters across blocks."""
    limit = _search_limit(doc) if max_pages is None else min(max_pages, doc.page_count)
    hits = []
    for page_no in range(1, limit + 1):
        page = doc[page_no - 1]
        groups = list(_page_lines(page)) + list(_cross_block_heading_rows(page))
        for group in groups:
            kind = classify_heading_line(_line_text(group))
            if not kind:
                continue
            y0 = min(word.box.y0 for word in group)
            if any(hit["page"] == page_no and abs(hit["y0"] - y0) <= 2 for hit in hits):
                continue
            hits.append({
                "page": page_no,
                "kind": kind,
                "y0": y0,
                "x0": min(word.box.x0 for word in group),
                "text": _line_text(group),
            })
    return hits


def _has_leader(text):
    """Detect textual TOC connectors: dots, dashes, and underscores."""
    return bool(re.search(r"(?:\.{3,}|·{2,}|…|[-–—_]{3,})", text))


def _point_xy(point):
    if hasattr(point, "x"):
        return point.x, point.y
    return point[0], point[1]


def detect_toc_vector_lines(page):
    """Identify horizontal PDF drawing segments.

    These segments can connect a section title with its page number.
    A drawn line is supporting geometric evidence, not proof of a TOC.
    Ordinary table rules are not treated as connectors by themselves.
    """
    segments = []
    for drawing in page.get_drawings():
        for item in drawing.get("items", ()):
            kind = item[0]
            if kind == "l":
                x0, y0 = _point_xy(item[1])
                x1, y1 = _point_xy(item[2])
                if abs(y0 - y1) <= 1.5 and abs(x0 - x1) >= 12:
                    segments.append((min(x0, x1), max(x0, x1), (y0 + y1) / 2))
            elif kind == "re":
                rect = item[1]
                if rect.height <= 2 and rect.width >= 12:
                    segments.append((rect.x0, rect.x1, (rect.y0 + rect.y1) / 2))
    return segments


def _vector_connects(segments, title_right, number_left, number_box):
    """Check a horizontal drawn segment within the title-to-reference gap."""
    gap = number_left - title_right
    if gap < 4:
        return False
    for x0, x1, line_y in segments:
        # PDF drawing baselines need not coincide with the word's top edge.
        if not (number_box.y0 - 2 <= line_y <= number_box.y1 + 2):
            continue
        overlap = min(x1, number_left) - max(x0, title_right)
        if overlap >= min(12, gap * 0.5):
            return True
    return False


def _entry(page_no, title_group, number_word, connector="spacing"):
    return {
        "page": page_no,
        "y0": min(title_group[0].box.y0, number_word.box.y0),
        "x0": title_group[0].box.x0,
        "x1": number_word.box.x1,
        "text": _line_text(title_group) + " " + number_word.text,
        "connector": connector,
    }


_ROMAN_PAGE = re.compile(
    r"^(?=[IVXLCDM]+$)M{0,4}(CM|CD|D?C{0,3})"
    r"(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$", re.I
)


def _page_number_kind(text):
    """Recognize Arabic and valid Roman numeral references, with punctuation."""
    token = text.strip().strip("()[]{}.,:")
    if re.fullmatch(r"[0-9]{1,5}", token):
        return "arabic"
    if _ROMAN_PAGE.fullmatch(token):
        return "roman"
    return None


def _title_words(group):
    return [word for word in group if re.search(r"[^\W\d_]", word.text, flags=re.UNICODE)]


def _aligned_groups(entries):
    """Group entries whose page numbers share one right-edge column."""
    groups = []
    for entry in entries:
        placed = False
        for group in groups:
            if abs(entry["x1"] - group[0]["x1"]) <= 2:
                group.append(entry)
                placed = True
                break
        if not placed:
            groups.append([entry])
    return [group for group in groups if len(group) >= 2]


def detect_toc_structure(doc, max_pages=None):
    """Find repeated title-and-page-reference rows (Arabic or Roman numerals).

    Textual, vector, and no-connector layouts are supported. A vector line
    is recorded as independent evidence rather than counting alone as a TOC.
    """
    limit = _search_limit(doc) if max_pages is None else min(max_pages, doc.page_count)
    pages = set()
    evidence = []
    for page_no in range(1, limit + 1):
        page = doc[page_no - 1]
        groups = _page_lines(page)
        segments = detect_toc_vector_lines(page)
        entries = []
        number_only = []
        titles = []
        for group in groups:
            if len(group) == 1 and _page_number_kind(group[0].text):
                number_only.append(group)
                continue
            if _title_words(group):
                titles.append(group)
            if len(group) < 2 or not _page_number_kind(group[-1].text) or _page_number_kind(group[-2].text):
                continue
            title = _title_words(group[:-1])
            if not title:
                continue
            number = group[-1]
            # Avoid confusing a numbered section heading with a TOC page ref.
            if (_page_number_kind(number.text) == "roman"
                    and _line_text(group[:-1]).strip().lower() in
                    {"section", "part", "chapter", "appendix", "annexure", "volume"}):
                continue
            text = _line_text(group)
            gap = number.box.x0 - group[-2].box.x1
            vector = _vector_connects(segments, title[-1].box.x1, number.box.x0, number.box)
            leader = _has_leader(text)
            if leader or gap >= 4 or vector:
                connector = "vector" if vector else "text-leader" if leader else "no-connector"
                entries.append(_entry(page_no, title, number, connector))
        for number_group in number_only:
            number = number_group[0]
            candidates = []
            for title_group in titles:
                title = _title_words(title_group)
                if not title or title[-1].box.x1 >= number.box.x0:
                    continue
                same_row = abs(title[0].box.y0 - number.box.y0) <= max(number.box.height, 3)
                if same_row:
                    candidates.append(title)
            if not candidates:
                continue
            title = max(candidates, key=lambda words: words[-1].box.x1)
            gap = number.box.x0 - title[-1].box.x1
            vector = _vector_connects(segments, title[-1].box.x1, number.box.x0, number.box)
            leader = _has_leader(_line_text(title))
            if leader or gap >= 4 or vector:
                connector = "vector" if vector else "text-leader" if leader else "no-connector"
                entries.append(_entry(page_no, title, number, connector))
        kept = [entry for group in _aligned_groups(entries) for entry in group]
        if not kept:
            continue
        pages.add(page_no)
        evidence.extend(kept)
    return pages, evidence


def detect_toc_region(doc):
    """Union heading and structural pages, then keep the consecutive span.

    Pages between the first and last detected page are part of the TOC even
    when neither detector marked them. The split on the first TOC page is the
    top of the earliest heading or structural entry on that page.
    """
    limit = _search_limit(doc)
    heading_hits = detect_toc_headings(doc, limit)
    structural_pages, structural_hits = detect_toc_structure(doc, limit)
    heading_pages = {hit["page"] for hit in heading_hits}
    detected = sorted(heading_pages | structural_pages)
    if not detected:
        raise ValueError("TOC could not be detected; no output was overwritten")
    toc_start, toc_end = detected[0], detected[-1]
    on_start = [hit["y0"] for hit in heading_hits if hit["page"] == toc_start]
    on_start += [hit["y0"] for hit in structural_hits if hit["page"] == toc_start]
    split_y = min(on_start) if on_start else doc[toc_start - 1].rect.y0
    return {
        "search_end": limit,
        "heading_pages": sorted(heading_pages),
        "structural_pages": sorted(structural_pages),
        "detected_pages": detected,
        "toc_start": toc_start,
        "toc_end": toc_end,
        "intermediate_pages": [page for page in range(toc_start, toc_end + 1) if page not in detected],
        "split_y": split_y,
        "heading_hits": heading_hits,
        "structural_hits": structural_hits,
    }


def normalize_prefix(source, count, destination: Path):
    """Normalize CropBox/rotation for the introductory pages and the TOC pages.

    `count` is the last physical page to include, which is the TOC end.
    Clear the copy's rotation first, then apply it explicitly while placing
    the visible CropBox. This avoids mixing a rotated page.rect with an
    unrotated PDF content coordinate system.
    The user's original document is never mutated.
    """
    normalized = pymupdf.open()
    working = pymupdf.open()
    try:
        if count:
            working.insert_pdf(source, from_page=0, to_page=count - 1)
        for i in range(count):
            old = working[i]
            rotation = old.rotation
            visible_size = pymupdf.Rect(old.rect)
            old.set_rotation(0)
            new = normalized.new_page(width=visible_size.width, height=visible_size.height)
            if old.get_contents():
                new.show_pdf_page(new.rect, working, i, rotate=-rotation)
            # Check character occurrences including digits and punctuation.
            # This catches crop/rotation loss before either engine is run.
            expected = Counter(ch for ch in source[i].get_text() if not ch.isspace())
            actual = Counter(ch for ch in new.get_text() if not ch.isspace())
            if expected != actual:
                raise ValueError(f"Page {i + 1}: coordinate normalization changed native "
                                 "text; refusing to produce a misleading comparison")
        if count:
            normalized.save(destination)
    finally:
        normalized.close()
        working.close()


def debug_page(page, tables, path):
    from PIL import Image as PILImage, ImageDraw
    scale = 1.3
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    image = PILImage.frombytes("RGB", [pix.width, pix.height], pix.samples)
    draw = ImageDraw.Draw(image)
    for table in tables:
        for cell in table.cells:
            draw.rectangle(tuple(x * scale for x in cell.box), outline="red", width=1)
        draw.rectangle(tuple(x * scale for x in table.box), outline="blue", width=2)
    image.save(path)


def render_vertical_span(page, words, tables, images, y0, y1, next_table, log):
    """Render the part of a page between y0 and y1, using the existing checks."""
    chosen_tables = [table for table in tables if y0 <= table.box.y0 < y1]
    owned_elsewhere = {
        word.id
        for table in tables if table not in chosen_tables
        for cell in table.cells for word in cell.words
    }
    forced = {
        word.id
        for table in chosen_tables
        for cell in table.cells for word in cell.words
    }
    selected_words = []
    for word in words:
        if word.id in owned_elsewhere:
            continue
        middle = (word.box.y0 + word.box.y1) / 2
        if word.id in forced or y0 <= middle < y1:
            selected_words.append(word)
    selected_images = [
        image for image in images
        if y0 <= (image.box.y0 + image.box.y1) / 2 < y1
    ]
    if not selected_words and not selected_images and not chosen_tables:
        return "", next_table
    return render_page(page, selected_words, chosen_tables, selected_images, next_table, log)


def extraction_to_html(source: str) -> str:
    """Turn the in-memory extraction markup into a one-page-at-a-time review.

    Each PDF page marker becomes its own sheet. Previous, next, and a
    jump box move between sheets. Rerun returns to the first sheet.
    Tables and images are embedded as HTML. No intermediate source file is
    published.
    """
    page_mark = re.compile(r"^<!-- PDF page (\d+) -->$")
    open_wrap = re.compile(r"^<(table|image)(\d+)>$")
    close_wrap = re.compile(r"^</(table|image)(\d+)>$")
    lines = [raw.rstrip() for raw in source.splitlines()]
    section_at = set()
    for index, line in enumerate(lines):
        if not line.startswith("# "):
            continue
        for follower in lines[index + 1:]:
            follower = follower.strip()
            if not follower:
                continue
            if page_mark.match(follower):
                section_at.add(index)
            break

    pages = []
    lead = []
    paragraph = []
    pending_section = ""

    def flush_paragraph(bucket):
        if not paragraph:
            return
        pieces = [escape(line) for line in paragraph]
        bucket.append("<p>" + "<br>\n".join(pieces) + "</p>")
        paragraph.clear()

    def bucket():
        return pages[-1][1] if pages else lead

    for index, line in enumerate(lines):
        if not line.strip():
            flush_paragraph(bucket())
            continue
        if index in section_at:
            flush_paragraph(bucket())
            pending_section = line[2:].strip()
            lead.append(f"<h1>{escape(pending_section)}</h1>")
            continue
        page = page_mark.match(line.strip())
        if page:
            flush_paragraph(bucket())
            pages.append((page.group(1), lead, pending_section))
            lead = []
            pending_section = ""
            continue
        opened = open_wrap.match(line.strip())
        if opened:
            flush_paragraph(bucket())
            kind = "table-block" if opened.group(1) == "table" else "image-block"
            bucket().append(f'<section class="{kind}">')
            continue
        if close_wrap.match(line.strip()):
            flush_paragraph(bucket())
            bucket().append("</section>")
            continue
        if line.lstrip().startswith("<"):
            flush_paragraph(bucket())
            bucket().append(line)
            continue
        if line.startswith("# "):
            flush_paragraph(bucket())
            bucket().append("<p>" + escape(line) + "</p>")
            continue
        paragraph.append(line)
    flush_paragraph(bucket())
    if lead and pages:
        pages[-1][1].extend(lead)
    elif lead and not pages:
        pages.append(("1", lead, pending_section))

    leaves = []
    for number, parts, section in pages:
        leaves.append(
            f'<section class="leaf" data-pdf="{escape(number, quote=True)}" '
            f'data-section="{escape(section, quote=True)}">\n' + "\n".join(parts) + "\n</section>"
        )
    if not leaves:
        leaves.append('<section class="leaf" data-pdf="1" data-section=""></section>')

    document = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DRHP Document Viewer</title>
</head>
<body bgcolor="#EDE9E1" text="#25211C" link="#76511F" vlink="#5C4630">
<font face="Inter, Arial, sans-serif">
<header>
<table width="100%" border="0" cellspacing="0" cellpadding="14" bgcolor="#28231F">
<tr>
<td>
<font face="Inter, Arial, sans-serif" color="#F8F2E7" size="5"><strong>DRHP Document Viewer</strong></font>
</td>
<td align="right" valign="middle">
<button type="button" id="first"><font face="Inter, Arial, sans-serif">&laquo; First</font></button>
<button type="button" id="prev"><font face="Inter, Arial, sans-serif">&larr; Previous</font></button>
&nbsp;
<label>
<font face="Inter, Arial, sans-serif" color="#F8F2E7"><strong>Page</strong></font>
<input id="jump" type="number" min="1" inputmode="numeric" size="4">
<font face="Inter, Arial, sans-serif" color="#F8F2E7">of <strong id="total">1</strong></font>
</label>
&nbsp;
<button type="button" id="next"><font face="Inter, Arial, sans-serif">Next &rarr;</font></button>
<button type="button" id="last"><font face="Inter, Arial, sans-serif">Last &raquo;</font></button>
</td>
</tr>
<tr>
<td colspan="2">
<font face="Inter, Arial, sans-serif" color="#D9C39E"><span id="section"></span></font>
</td>
</tr>
</table>
</header>
<br>
<main>
<center>
<table width="92%" border="1" cellspacing="0" cellpadding="24" bgcolor="#FFFDF8">
<tr><td>
<article id="sheet">
"""
    script = """
</article>
</td></tr>
</table>
</center>
</main>
<br>
<hr>
<footer align="center">
<font face="Inter, Arial, sans-serif" size="2">Use First, Previous, Page, Next, or Last to navigate.</font>
</footer>
</font>
<br>
<script>
const leaves = Array.from(document.querySelectorAll(".leaf"));
const sheet = document.getElementById("sheet");
const section = document.getElementById("section");
const jump = document.getElementById("jump");
const total = document.getElementById("total");
const buttons = {
  first: document.getElementById("first"),
  prev: document.getElementById("prev"),
  next: document.getElementById("next"),
  last: document.getElementById("last")
};
let index = 0;
const stored = Number(sessionStorage.getItem("drhp-review-page"));
if (stored >= 0 && stored < leaves.length) index = stored;

function show(next, direction) {
  index = Math.max(0, Math.min(leaves.length - 1, next));
  leaves.forEach((leaf, i) => { leaf.hidden = i !== index; });
  const leaf = leaves[index];
  section.textContent = leaf.dataset.section || "";
  jump.value = leaf.dataset.pdf;
  jump.max = leaves[leaves.length - 1].dataset.pdf;
  total.textContent = leaves.length;
  buttons.first.disabled = index === 0;
  buttons.prev.disabled = index === 0;
  buttons.next.disabled = index === leaves.length - 1;
  buttons.last.disabled = index === leaves.length - 1;
  window.scrollTo(0, 0);
  sessionStorage.setItem("drhp-review-page", String(index));
}
function step(direction) { show(index + direction, direction); }
buttons.first.addEventListener("click", () => show(0));
buttons.prev.addEventListener("click", () => step(-1));
buttons.next.addEventListener("click", () => step(1));
buttons.last.addEventListener("click", () => show(leaves.length - 1));
jump.addEventListener("change", () => {
  const wanted = Number(jump.value);
  const found = leaves.findIndex((leaf) => Number(leaf.dataset.pdf) === wanted);
  if (found >= 0) show(found, found > index ? 1 : -1);
  else jump.value = leaves[index].dataset.pdf;
});
document.addEventListener("keydown", (event) => {
  if (event.target === jump) return;
  if (event.key === "ArrowRight") step(1);
  if (event.key === "ArrowLeft") step(-1);
  if (event.key === "Home") show(0);
  if (event.key === "End") show(leaves.length - 1);
});
show(index, 0);
</script>
</body>
</html>
"""
    return document + "\n".join(leaves) + script


def _make_stage(parent: Path, prefix: str) -> Path:
    """Create a private work directory without tempfile's Windows ACL changes."""
    for _attempt in range(100):
        candidate = parent / f"{prefix}{secrets.token_hex(6)}"
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            continue
    raise FileExistsError(f"Could not create a unique staging directory in {parent}")


def run_ab(pdf_path: Path | str, output_dir: Path | str | None = None, *,
           debug: bool = False,
           html_name: str | None = None):
    """Run the existing extractor and write the finished files into output_dir.

    No automatic repairs. A renderer failure aborts this candidate; it is NOT
    converted into a successful table-free result, and the previous HTML,
    images, and debug previews stay in place.
    """
    pdf_path = Path(pdf_path)
    output_dir = HERE / "output" if output_dir is None else Path(output_dir)
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)
    html_name = html_name or f"{pdf_path.stem}.html"
    if Path(html_name).name != html_name or not html_name.lower().endswith(".html"):
        raise ValueError("html_name must be a filename ending in .html")

    output_dir.mkdir(parents=True, exist_ok=True)
    # Keep temporary work beside the destination so Windows permissions and
    # the final atomic replacements use the same writable filesystem.
    staging = _make_stage(output_dir, ".pdf_table_stage_")

    # Existing output is left alone until a complete package is ready.
    baseline_paths = [output_dir / html_name, output_dir / "run_log.txt"]
    for folder_name in ("images", "debug"):
        folder = output_dir / folder_name
        if folder.is_dir():
            baseline_paths += sorted(p for p in folder.rglob("*") if p.is_file())
    protected = {p: sha256(p.read_bytes()).hexdigest() for p in baseline_paths if p.is_file()}

    log = [f"Source: {pdf_path.resolve()}", "Engine: camelot",
           "Finished files are written directly in the output folder.",
           "No hierarchy JSON. No API. No OCR."]
    table_number = 0
    status = "FAILED"
    error = None
    result_ready = False
    print(f"Running Camelot into {output_dir}", flush=True)

    try:
        for name in ("PyMuPDF", "camelot-py"):
            try:
                log.append(f"{name}: {metadata.version(name)}")
            except metadata.PackageNotFoundError:
                log.append(f"{name}: not installed")
        guard_dir = staging / "guard"
        guard_dir.mkdir()
        with pymupdf.open(pdf_path) as original:
            page_count = original.page_count
            if not page_count:
                raise ValueError("Input PDF has no pages")
            # Testing phase: first 50 physical pages only. Delete this line for the full PDF.
            page_count = min(page_count, 50)

            # TOC detection only labels sections. Extraction covers every page.
            try:
                region = detect_toc_region(original)
            except ValueError as exc:
                if "TOC could not be detected" not in str(exc):
                    raise
                region = None

            if region is None:
                toc_start = toc_end = None
                log += [
                    f"TOC search window: pages 1-{min(MAX_TOC_SEARCH_PAGES, page_count)}",
                    "TOC not detected; extracting all pages under Main Document",
                ]
            else:
                toc_start = region["toc_start"]
                toc_end = region["toc_end"]
                intro_label = f"pages 1-{toc_start - 1}" if toc_start > 1 else "none"
                log += [
                    f"TOC search window: pages 1-{region['search_end']}",
                    f"Heading detections: {region['heading_pages']}",
                    f"Structural detections: {region['structural_pages']}",
                    f"Combined detections: {region['detected_pages']}",
                    "",
                    f"TOC start page: {toc_start}",
                    f"TOC end page: {toc_end}",
                    f"TOC range: {toc_start}-{toc_end}",
                    f"Intermediate pages included: {region['intermediate_pages']}",
                    "",
                    f"Introductory Information: {intro_label}",
                    f"TOC start y on page {toc_start}: {region['split_y']:.1f}",
                ]
                connector_counts = Counter(hit.get("connector", "unknown") for hit in region["structural_hits"])
                log.append(f"TOC structural evidence types: {dict(connector_counts)}")

            log.append(f"Extraction range: physical pages 1-{page_count} (testing limit, not the entire PDF)")
            if toc_end is not None:
                log.append(f"Main Document: pages {toc_end + 1}-{page_count}"
                           if toc_end < page_count else "Main Document: no pages after detected TOC")

            normalized_path = guard_dir / "normalized_document.pdf"
            normalize_prefix(original, page_count, normalized_path)
            with pymupdf.open(normalized_path) as doc:
                # Save images ONLY in this isolated package.
                images = extract_images(doc, staging / "images", log)
                words = {i: read_words(page) for i, page in enumerate(doc)}
                proposed = detect_camelot(normalized_path, page_count,
                                          [page.rect.height for page in doc], log)
                intro = ["# Introductory Information"]
                contents = ["# Table of Contents"]
                body = ["# Main Document"]
                for i, page in enumerate(doc):
                    selected = select_tables(proposed[i], words[i], log)
                    page_no = i + 1
                    top = page.rect.y0
                    bottom = page.rect.y1 + 1
                    if region is None or page_no > toc_end:
                        content, table_number = render_vertical_span(
                            page, words[i], selected, images[i], top, bottom, table_number, log)
                        if content:
                            body.append(f"<!-- PDF page {page_no} -->\n\n{content}")
                    else:
                        split = region["split_y"] if page_no == toc_start else top
                        if page_no < toc_start or (page_no == toc_start and split > top):
                            y1 = split if page_no == toc_start else bottom
                            content, table_number = render_vertical_span(
                                page, words[i], selected, images[i], top, y1, table_number, log)
                            if content:
                                intro.append(f"<!-- PDF page {page_no} -->\n\n{content}")
                        if page_no >= toc_start:
                            y0 = split if page_no == toc_start else top
                            content, table_number = render_vertical_span(
                                page, words[i], selected, images[i], y0, bottom, table_number, log)
                            if content:
                                contents.append(f"<!-- PDF page {page_no} -->\n\n{content}")

                    if debug:
                        try:
                            debug_folder = staging / "debug"
                            debug_folder.mkdir(exist_ok=True)
                            debug_page(page, selected, debug_folder / f"page_{page_no:04d}.png")
                        except Exception as exc:
                            log.append(f"PAGE {page_no}: debug image unavailable: {type(exc).__name__}: {exc}")

                if region is None:
                    document_parts = body
                else:
                    document_parts = intro + [""] + contents
                    if len(body) > 1:
                        document_parts += [""] + body
                result = "\n\n".join(part for part in document_parts if part != "").rstrip() + "\n"

        for path, digest in protected.items():
            if not path.is_file() or sha256(path.read_bytes()).hexdigest() != digest:
                raise RuntimeError(f"Existing baseline file changed during this run: {path}")
        log.append(f"Existing baseline package: {len(protected)} protected files verified unchanged.")
        (staging / html_name).write_text(extraction_to_html(result), encoding="utf-8")
        result_ready = True
        log += [f"Tables emitted: {table_number}", "Renderer fallback pages: []"]
        status = "SUCCESS"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        log.append(f"ERROR: {error}")
        # No partial HTML is labelled a completed output.
        (staging / html_name).unlink(missing_ok=True)
        result_ready = False

    log += [f"STATUS: {status}", f"Output folder: {output_dir}",
            "A failed run does not replace the HTML, images, or debug previews.",
            "Successful execution is not proof of correct grouping on every PDF."]
    (staging / "run_log.txt").write_text("\n".join(log) + "\n", encoding="utf-8")
    try:
        if result_ready:
            _publish_tree(staging / "images", output_dir / "images")
            if (staging / "debug").is_dir():
                _publish_tree(staging / "debug", output_dir / "debug")
            _publish_file(staging / html_name, output_dir / html_name)
            # Clean up the document produced by older Markdown-first versions.
            (output_dir / f"{Path(html_name).stem}.md").unlink(missing_ok=True)
        _publish_file(staging / "run_log.txt", output_dir / "run_log.txt")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    result_status = {
        "ok": result_ready,
        "status": status,
        "run_dir": str(output_dir),
        "log": str(output_dir / "run_log.txt"),
        "tables": table_number,
    }
    if result_ready:
        result_status["html"] = str(output_dir / html_name)
    if error:
        result_status["error"] = error
    print(f"Camelot: {status}; see {result_status['log']}", flush=True)
    return {"camelot": result_status}


def _publish_file(source: Path, destination: Path) -> None:
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(source.read_bytes())
    temporary.replace(destination)


def _publish_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        return
    incoming = destination.with_name(destination.name + ".incoming")
    if incoming.exists():
        shutil.rmtree(incoming)
    shutil.copytree(source, incoming)
    previous = destination.with_name(destination.name + ".previous")
    if previous.exists():
        shutil.rmtree(previous)
    if destination.exists():
        destination.rename(previous)
        try:
            incoming.rename(destination)
        except Exception:
            previous.rename(destination)
            raise
        shutil.rmtree(previous)
    else:
        incoming.rename(destination)


def _document_slug(pdf_path: Path) -> str:
    """Return a filesystem-safe, stable namespace for one source PDF."""
    slug = re.sub(r"[^a-z0-9]+", "_", pdf_path.stem.casefold()).strip("_")
    if not slug:
        raise ValueError(f"Could not create a document namespace for {pdf_path.name!r}")
    return slug


def _document_title(pdf_path: Path) -> str:
    """Turn a PDF filename into a readable tab label."""
    acronyms = {"drhp": "DRHP", "lic": "LIC", "ipo": "IPO"}
    words = re.split(r"[_\-\s]+", pdf_path.stem)
    return " ".join(acronyms.get(word.casefold(), word if any(c.isupper() for c in word[1:])
                                 else word.capitalize()) for word in words if word)


def _article_fragment(document_html: str) -> str:
    """Read only the generated page leaves from a single-document viewer."""
    start_marker = '<article id="sheet">\n'
    end_marker = "\n</article>"
    start = document_html.find(start_marker)
    end = document_html.rfind(end_marker)
    if start < 0 or end < 0 or end <= start:
        raise ValueError("Generated document HTML does not contain the expected article")
    return document_html[start + len(start_marker):end]


def collection_to_html(documents: list[dict[str, str]]) -> str:
    """Build one CSS-free HTML viewer with a tab for every extracted PDF."""
    if not documents:
        raise ValueError("At least one document is required")

    several = len(documents) > 1
    page_title = escape("Indian Market DRHP Collection" if several else documents[0]["title"])
    footer = ("Choose a company tab, then use First, Previous, Page, Next, or Last."
              if several else "Use First, Previous, Page, Next, or Last.")
    tab_buttons = []
    panels = []
    for index, document in enumerate(documents):
        title = escape(document["title"])
        fragment = document["fragment"]
        tab_buttons.append(
            f'<button type="button" class="document-tab" data-document-index="{index}" '
            f'aria-selected="false"><font face="Inter, Arial, sans-serif">{title}</font></button>'
        )
        panels.append(f'''<section class="document-panel" data-document-index="{index}" hidden>
<table width="100%" border="0" cellspacing="0" cellpadding="12" bgcolor="#DDD4C5">
<tr>
<td><font face="Inter, Arial, sans-serif" size="4"><strong>{title}</strong></font></td>
<td align="right">
<button type="button" data-action="first"><font face="Inter, Arial, sans-serif">&laquo; First</font></button>
<button type="button" data-action="prev"><font face="Inter, Arial, sans-serif">&larr; Previous</font></button>
&nbsp;
<label><font face="Inter, Arial, sans-serif"><strong>Page</strong></font>
<input data-role="jump" type="number" min="1" inputmode="numeric" size="4">
<font face="Inter, Arial, sans-serif">of <strong data-role="total">1</strong></font></label>
&nbsp;
<button type="button" data-action="next"><font face="Inter, Arial, sans-serif">Next &rarr;</font></button>
<button type="button" data-action="last"><font face="Inter, Arial, sans-serif">Last &raquo;</font></button>
</td>
</tr>
<tr><td colspan="2"><font face="Inter, Arial, sans-serif" color="#604A2D" data-role="section"></font></td></tr>
</table>
<br>
<table width="100%" border="1" cellspacing="0" cellpadding="24" bgcolor="#FFFDF8">
<tr><td><div data-role="pages">{fragment}</div></td></tr>
</table>
</section>''')

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{page_title}</title>
</head>
<body bgcolor="#EDE9E1" text="#25211C" link="#76511F" vlink="#5C4630">
<font face="Inter, Arial, sans-serif">
<header>
<table width="100%" border="0" cellspacing="0" cellpadding="14" bgcolor="#28231F">
<tr><td><font face="Inter, Arial, sans-serif" color="#F8F2E7" size="5"><strong>{page_title}</strong></font></td></tr>
{f'<tr><td>{" ".join(tab_buttons)}</td></tr>' if several else ''}
</table>
</header>
<br>
<main><center><table width="96%" border="0" cellspacing="0" cellpadding="0"><tr><td>
{''.join(panels)}
</td></tr></table></center></main>
<br><hr>
<footer align="center"><font face="Inter, Arial, sans-serif" size="2">{footer}</font></footer>
</font>
<script>
const tabs = Array.from(document.querySelectorAll(".document-tab"));
const panels = Array.from(document.querySelectorAll(".document-panel"));
let activeDocument = Number(sessionStorage.getItem("drhp-collection-document") || 0);
let pageState = {{}};
try {{ pageState = JSON.parse(sessionStorage.getItem("drhp-collection-pages") || "{{}}"); }} catch (_error) {{ pageState = {{}}; }}

function leavesFor(panel) {{
  return Array.from(panel.querySelector('[data-role="pages"]').children)
    .filter((element) => element.classList.contains("leaf"));
}}

function saveState() {{
  sessionStorage.setItem("drhp-collection-document", String(activeDocument));
  sessionStorage.setItem("drhp-collection-pages", JSON.stringify(pageState));
}}

function showPage(panel, requestedIndex) {{
  const leaves = leavesFor(panel);
  if (!leaves.length) return;
  const documentIndex = panel.dataset.documentIndex;
  const pageIndex = Math.max(0, Math.min(leaves.length - 1, requestedIndex));
  pageState[documentIndex] = pageIndex;
  leaves.forEach((leaf, index) => {{ leaf.hidden = index !== pageIndex; }});
  const leaf = leaves[pageIndex];
  const jump = panel.querySelector('[data-role="jump"]');
  jump.value = leaf.dataset.pdf;
  jump.max = leaves[leaves.length - 1].dataset.pdf;
  panel.querySelector('[data-role="total"]').textContent = leaves.length;
  panel.querySelector('[data-role="section"]').textContent = leaf.dataset.section || "";
  panel.querySelector('[data-action="first"]').disabled = pageIndex === 0;
  panel.querySelector('[data-action="prev"]').disabled = pageIndex === 0;
  panel.querySelector('[data-action="next"]').disabled = pageIndex === leaves.length - 1;
  panel.querySelector('[data-action="last"]').disabled = pageIndex === leaves.length - 1;
  saveState();
  window.scrollTo(0, 0);
}}

function activateDocument(requestedIndex) {{
  activeDocument = Math.max(0, Math.min(panels.length - 1, requestedIndex));
  panels.forEach((panel, index) => {{ panel.hidden = index !== activeDocument; }});
  tabs.forEach((tab, index) => {{
    const selected = index === activeDocument;
    tab.disabled = selected;
    tab.setAttribute("aria-selected", String(selected));
  }});
  const panel = panels[activeDocument];
  showPage(panel, Number(pageState[panel.dataset.documentIndex] || 0));
}}

tabs.forEach((tab) => tab.addEventListener("click", () => activateDocument(Number(tab.dataset.documentIndex))));
panels.forEach((panel) => {{
  panel.addEventListener("click", (event) => {{
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const leaves = leavesFor(panel);
    const current = Number(pageState[panel.dataset.documentIndex] || 0);
    const targets = {{ first: 0, prev: current - 1, next: current + 1, last: leaves.length - 1 }};
    showPage(panel, targets[button.dataset.action]);
  }});
  panel.querySelector('[data-role="jump"]').addEventListener("change", (event) => {{
    const leaves = leavesFor(panel);
    const wanted = Number(event.target.value);
    const found = leaves.findIndex((leaf) => Number(leaf.dataset.pdf) === wanted);
    if (found >= 0) showPage(panel, found);
    else showPage(panel, Number(pageState[panel.dataset.documentIndex] || 0));
  }});
}});
document.addEventListener("keydown", (event) => {{
  if (event.target.matches('input')) return;
  const panel = panels[activeDocument];
  const leaves = leavesFor(panel);
  const current = Number(pageState[panel.dataset.documentIndex] || 0);
  if (event.key === "ArrowRight") showPage(panel, current + 1);
  if (event.key === "ArrowLeft") showPage(panel, current - 1);
  if (event.key === "Home") showPage(panel, 0);
  if (event.key === "End") showPage(panel, leaves.length - 1);
}});
activateDocument(activeDocument);
</script>
</body>
</html>
'''


def run_collection(pdf_paths, output_dir: Path | str | None = None, *,
                   collection_name: str = "drhp_collection.html", debug: bool = True):
    """Extract PDFs independently and publish one namespaced, tabbed package."""
    pdf_paths = [Path(path) for path in pdf_paths]
    if not pdf_paths:
        raise ValueError("No PDF inputs were provided")
    for pdf_path in pdf_paths:
        if not pdf_path.is_file():
            raise FileNotFoundError(pdf_path)
    if Path(collection_name).name != collection_name or not collection_name.lower().endswith(".html"):
        raise ValueError("collection_name must be a filename ending in .html")

    output_dir = HERE / "output" if output_dir is None else Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    staging = _make_stage(output_dir, ".collection_stage_")
    result = {
        "ok": False,
        "status": "FAILED",
        "run_dir": str(output_dir),
        "documents": 0,
    }
    try:
        documents = []
        slugs = set()
        for position, pdf_path in enumerate(pdf_paths, start=1):
            slug = _document_slug(pdf_path)
            if slug in slugs:
                raise ValueError(f"Duplicate document namespace: {slug}")
            slugs.add(slug)
            print(f"Collection document {position}/{len(pdf_paths)}: {pdf_path.name}", flush=True)

            document_output = staging / "work" / slug
            status = run_ab(pdf_path, document_output, debug=debug,
                            html_name="document.html")["camelot"]
            log_source = document_output / "run_log.txt"
            if not status["ok"]:
                detail = status.get("error", "unknown extraction error")
                raise RuntimeError(f"{pdf_path.name}: {detail}")

            generated = Path(status["html"]).read_text(encoding="utf-8")
            fragment = _article_fragment(generated)
            fragment = fragment.replace('<section class="leaf"', '<section hidden class="leaf"')
            fragment = fragment.replace('src="images/', f'src="images/{slug}/')
            documents.append({
                "title": _document_title(pdf_path),
                "fragment": fragment,
            })

            shutil.copytree(document_output / "images", staging / "images" / slug)
            if debug:
                debug_source = document_output / "debug"
                if not debug_source.is_dir():
                    raise RuntimeError(f"{pdf_path.name}: debug previews were not produced")
                shutil.copytree(debug_source, staging / "debug" / slug)
            log_destination = staging / "logs" / slug
            log_destination.mkdir(parents=True, exist_ok=True)
            shutil.copy2(log_source, log_destination / "run_log.txt")

        (staging / collection_name).write_text(collection_to_html(documents), encoding="utf-8")
        _publish_tree(staging / "images", output_dir / "images")
        if debug:
            _publish_tree(staging / "debug", output_dir / "debug")
        _publish_tree(staging / "logs", output_dir / "logs")
        _publish_file(staging / collection_name, output_dir / collection_name)

        for legacy in output_dir.glob("*.html"):
            if legacy.name != collection_name:
                legacy.unlink()
        for legacy in output_dir.glob("*.md"):
            legacy.unlink()
        (output_dir / "run_log.txt").unlink(missing_ok=True)

        result.update({
            "ok": True,
            "status": "SUCCESS",
            "html": str(output_dir / collection_name),
            "documents": len(documents),
        })
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    print(f"Collection: {result['status']}", flush=True)
    return result


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=HERE / "input" / "sagility-india-drhp-final.pdf")
    parser.add_argument("--out", type=Path, default=HERE / "output")
    parser.add_argument("--debug", action="store_true", help="Write cell-boundary PNGs for review")
    args = parser.parse_args()
    default_pdf = HERE / "input" / "sagility-india-drhp-final.pdf"
    html_name = "sagility_drhp.html" if args.pdf.resolve() == default_pdf.resolve() else None
    results = run_ab(args.pdf, args.out, debug=args.debug, html_name=html_name)
    if not all(status["ok"] for status in results.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    cli()
