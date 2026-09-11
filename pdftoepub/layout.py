from __future__ import annotations

from dataclasses import dataclass, field

from pdftoepub.models import Block

# Full-width items stay as section anchors; remaining text is split at a
# vertical gutter and read left column, then right.

_MIN_COLUMN_LINES = 3
_MIN_COLUMN_CHARS = 20
_GUTTER_LOW = 0.32
_GUTTER_HIGH = 0.68
_FULL_WIDTH_RATIO = 0.58
_ROW_Y_TOL = 8.0
_MAX_TABLE_CELL = 80
_MAX_TWO_COL_CELL = 36
_MIN_TABLE_ROWS = 2
_MIN_TABLE_COLS = 2
_ALIGN_TOL = 36.0


@dataclass
class PageUnit:
    y: float
    x: float
    kind: str
    text: str = ""
    size: float = 11.0
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    page_index: int = 0
    page_height: float = 0.0
    page_width: float = 612.0
    flags: int = 0
    cells: list[tuple[float, str]] = field(default_factory=list)
    image: object | None = None


def cells_from_spans(spans: list[dict], normalize) -> list[tuple[float, str]]:
    pieces: list[tuple[tuple[float, float, float, float], str]] = []
    for span in spans:
        text = normalize(span.get("text") or "")
        if not text.strip() and not pieces:
            continue
        bbox = tuple(span.get("bbox") or (0, 0, 0, 0))
        pieces.append((bbox, text))
    if not pieces:
        return []

    cells: list[tuple[float, str]] = []
    current_text = pieces[0][1]
    current_x = float(pieces[0][0][0])
    prev_x1 = float(pieces[0][0][2])
    for bbox, text in pieces[1:]:
        gap = float(bbox[0]) - prev_x1
        width = max(prev_x1 - current_x, 1.0)
        char_w = width / max(len(current_text.strip()) or 1, 1)
        if gap > max(14.0, char_w * 2.2):
            cleaned = normalize(current_text).strip()
            if cleaned:
                cells.append((current_x, cleaned))
            current_text = text
            current_x = float(bbox[0])
        else:
            current_text += text
        prev_x1 = float(bbox[2])
    cleaned = normalize(current_text).strip()
    if cleaned:
        cells.append((current_x, cleaned))
    return cells


def detect_column_gutter(units: list[PageUnit], page_width: float) -> float | None:
    """Return a split-x if the page looks like two text columns."""
    candidates = [
        unit
        for unit in units
        if unit.kind == "text" and len(unit.text) >= _MIN_COLUMN_CHARS
    ]
    if len(candidates) < _MIN_COLUMN_LINES * 2:
        return None

    width = max(float(page_width), 1.0)
    bins = max(32, min(int(width), 800))
    occupancy = [0] * bins

    for unit in candidates:
        x0, _, x1, _ = unit.bbox
        if (x1 - x0) >= width * _FULL_WIDTH_RATIO:
            continue
        left = max(0, min(bins - 1, int(x0 / width * bins)))
        right = max(left + 1, min(bins, int(x1 / width * bins)))
        for index in range(left, right):
            occupancy[index] += 1

    lo = int(bins * _GUTTER_LOW)
    hi = int(bins * _GUTTER_HIGH)
    best_gap = 0
    best_start = -1
    start = None
    for index in range(lo, hi):
        if occupancy[index] == 0:
            if start is None:
                start = index
        elif start is not None:
            gap = index - start
            if gap > best_gap:
                best_gap = gap
                best_start = start
            start = None
    if start is not None:
        gap = hi - start
        if gap > best_gap:
            best_gap = gap
            best_start = start

    if best_start < 0 or best_gap < max(4, int(bins * 0.03)):
        return None

    gutter = ((best_start + best_gap / 2) / bins) * width
    left_n = sum(1 for unit in candidates if unit.bbox[2] <= gutter + 4)
    right_n = sum(1 for unit in candidates if unit.bbox[0] >= gutter - 4)
    if left_n < _MIN_COLUMN_LINES or right_n < _MIN_COLUMN_LINES:
        return None
    return gutter


def reorder_two_column(units: list[PageUnit], gutter: float, page_width: float) -> list[PageUnit]:
    def is_full(unit: PageUnit) -> bool:
        x0, _, x1, _ = unit.bbox if unit.kind == "text" else (unit.x, unit.y, unit.x + 1, unit.y + 1)
        if unit.kind == "image":
            return x0 < gutter < x1 or (x1 - x0) >= page_width * _FULL_WIDTH_RATIO
        return (x1 - x0) >= page_width * _FULL_WIDTH_RATIO or (x0 < gutter < x1)

    result: list[PageUnit] = []
    pending_left: list[PageUnit] = []
    pending_right: list[PageUnit] = []

    def flush() -> None:
        result.extend(sorted(pending_left, key=lambda item: item.y))
        result.extend(sorted(pending_right, key=lambda item: item.y))
        pending_left.clear()
        pending_right.clear()

    for unit in sorted(units, key=lambda item: (item.y, item.x)):
        if is_full(unit):
            flush()
            result.append(unit)
        elif unit.x < gutter:
            pending_left.append(unit)
        else:
            pending_right.append(unit)
    flush()
    return result


def try_parse_table(units: list[PageUnit]) -> tuple[Block | None, int]:
    rows: list[list[str]] = []
    xs_rows: list[list[float]] = []
    consumed = 0
    page = None
    index = 0
    while index < len(units):
        unit = units[index]
        if unit.kind != "text":
            break
        if page is None:
            page = unit.page_index
        if unit.page_index != page:
            break
        row_units = [unit]
        look = index + 1
        while look < len(units) and units[look].kind == "text":
            other = units[look]
            if other.page_index != page or not _same_row(unit, other):
                break
            row_units.append(other)
            look += 1
        cells, xs = _cells_from_row(row_units)
        if not _row_is_table_cells(cells):
            break
        rows.append(cells)
        xs_rows.append(xs)
        consumed = look
        index = look
    if len(rows) < _MIN_TABLE_ROWS or not _columns_aligned(xs_rows):
        return None, 0
    text = "\n".join("\t".join(row) for row in rows)
    return Block(kind="table", text=text, rows=rows), consumed


def find_tables(units: list[PageUnit]) -> list[tuple[int, int, Block]]:
    found: list[tuple[int, int, Block]] = []
    index = 0
    while index < len(units):
        block, count = try_parse_table(units[index:])
        if block is not None and count:
            found.append((index, index + count, block))
            index += count
        else:
            index += 1
    return found


def layout_page(units: list[PageUnit], page_width: float) -> list[PageUnit | Block]:
    """Keep tables intact, then read two-column leftovers left-then-right."""
    tables = {start: (end, block) for start, end, block in find_tables(units)}
    result: list[PageUnit | Block] = []
    index = 0
    while index < len(units):
        if index in tables:
            end, block = tables[index]
            result.append(block)
            index = end
            continue
        chunk: list[PageUnit] = []
        look = index
        while look < len(units) and look not in tables:
            chunk.append(units[look])
            look += 1
        gutter = detect_column_gutter(chunk, page_width)
        if gutter is not None:
            result.extend(reorder_two_column(chunk, gutter, page_width))
        else:
            result.extend(chunk)
        index = look
    return result


def _row_is_table_cells(cells: list[str]) -> bool:
    if len(cells) < _MIN_TABLE_COLS:
        return False
    if any(len(cell) > _MAX_TABLE_CELL for cell in cells):
        return False
    if len(cells) == 2:
        if any(len(cell) > _MAX_TWO_COL_CELL for cell in cells):
            return False
        if any(cell.rstrip().endswith(tuple(".?!")) for cell in cells):
            return False
    return True


def _same_row(left: PageUnit, right: PageUnit) -> bool:
    left_mid = (left.bbox[1] + left.bbox[3]) / 2
    right_mid = (right.bbox[1] + right.bbox[3]) / 2
    tol = max(_ROW_Y_TOL, abs(left.bbox[3] - left.bbox[1]) * 0.65)
    return abs(left_mid - right_mid) <= tol


def _cells_from_row(units: list[PageUnit]) -> tuple[list[str], list[float]]:
    pieces: list[tuple[float, str]] = []
    for unit in units:
        if unit.cells:
            pieces.extend(unit.cells)
        else:
            pieces.append((unit.x, unit.text))
    pieces.sort(key=lambda item: item[0])
    merged: list[tuple[float, str]] = []
    for x, text in pieces:
        text = text.strip()
        if not text:
            continue
        if merged and abs(x - merged[-1][0]) < 8:
            merged[-1] = (merged[-1][0], f"{merged[-1][1]} {text}".strip())
        else:
            merged.append((x, text))
    return [text for _, text in merged], [x for x, _ in merged]


def _columns_aligned(xs_rows: list[list[float]]) -> bool:
    if len(xs_rows) < _MIN_TABLE_ROWS:
        return False
    lengths = [len(xs) for xs in xs_rows]
    n = max(set(lengths), key=lengths.count)
    if n < _MIN_TABLE_COLS:
        return False
    aligned = [xs for xs in xs_rows if len(xs) == n]
    if len(aligned) < _MIN_TABLE_ROWS:
        return False
    for col in range(n):
        vals = [xs[col] for xs in aligned]
        if max(vals) - min(vals) > _ALIGN_TOL:
            return False
    return True
