"""Read a PDF page as plain text, in reading order.

Shared by CV extraction and the knowledge-base index. It is copied into the
Docker ``index`` stage, so it must not import other app modules.

On top of pdfplumber's own extraction this handles two layouts it reads out of
order: sideways text (a landscape table on a portrait page, a turned column
heading), and two columns side by side (a CV's sidebar, a report's two-column
body). Text in each orientation is read separately, the most common first.
"""

from pdfplumber.utils import extract_text, extract_words

# A column gutter must be clear of words for this far either side of the split
# line. It is wider than the space between words, so ordinary lines of text
# always cross it.
GUTTER_HALF_WIDTH = 6
# The split line is looked for in this middle band of the page width.
SPLIT_RANGE = (0.2, 0.8)
# Each column must hold at least this share of the page's text.
MIN_COLUMN_SHARE = 0.1


def _orientations(page) -> list[tuple[list[dict], float, float]]:
    """Split the page's characters by orientation, each group turned upright.

    Returns (chars, width, height) per group, largest first: upright text,
    text turned anticlockwise (running up the page) and text turned clockwise
    (running down it), so sideways text, from a landscape table or a turned
    column heading, reads in the usual order.
    """
    width, height = page.width, page.height
    upright, left, right = [], [], []
    for c in page.chars:
        if c["upright"]:
            upright.append(c)
        elif c["matrix"][1] > 0:  # the matrix's second entry is the turn's sine
            box = {"x0": height - c["bottom"], "x1": height - c["top"], "top": c["x0"], "bottom": c["x1"]}
            left.append({**c, **box, "doctop": box["top"], "upright": True})
        else:
            box = {"x0": c["top"], "x1": c["bottom"], "top": width - c["x1"], "bottom": width - c["x0"]}
            right.append({**c, **box, "doctop": box["top"], "upright": True})
    groups = [(upright, width, height), (left, height, width), (right, height, width)]
    return sorted((g for g in groups if g[0]), key=lambda g: -len(g[0]))


def _find_columns(phrases, width, height):
    """Find a two-column region on the page.

    phrases are runs of words with no gutter-wide gap between them. Returns
    (split_x, top, bottom): the columns sit side by side between top and
    bottom, split at split_x, with full-width text (a name heading, a page
    footer) above and below. Returns None for a single-column page.
    """
    total = sum(len(p["text"]) for p in phrases)
    best, best_score = None, None
    lo, hi = (int(width * f) for f in SPLIT_RANGE)
    for x in range(lo, hi, 2):
        # Phrases crossing the split line are full-width rows; the columns are
        # the tallest stretch of the page between them.
        crossing = sorted((p["top"], p["bottom"]) for p in phrases if p["x0"] < x < p["x1"])
        edges = [(0, 0), *crossing, (height, height)]
        top, bottom, reach = 0, 0, 0
        for (_, prev_bottom), (next_top, _) in zip(edges, edges[1:]):
            reach = max(reach, prev_bottom)
            if next_top - reach > bottom - top:
                top, bottom = reach, next_top
        inside = [p for p in phrases if top <= (p["top"] + p["bottom"]) / 2 <= bottom]
        left = [p for p in inside if p["x1"] <= x - GUTTER_HALF_WIDTH]
        right = [p for p in inside if p["x0"] >= x + GUTTER_HALF_WIDTH]
        if len(left) + len(right) < len(inside):
            continue  # text runs into the gutter
        if min(sum(len(p["text"]) for p in side) for side in (left, right)) < MIN_COLUMN_SHARE * total:
            continue
        # The real split has the widest clear gutter. A narrower one means
        # full-width text (a centred name, say) was counted as column text.
        gutter = min(p["x0"] for p in right) - max(p["x1"] for p in left)
        score = (gutter, len(inside))
        if best_score is None or score > best_score:
            best, best_score = (x, top, bottom), score
    return best


def _text(chars, width, height) -> str:
    # Spaces are kept so a line of words reads as one phrase, broken only by a
    # gap wide enough to be a gutter.
    phrases = extract_words(chars, keep_blank_chars=True, x_tolerance=2 * GUTTER_HALF_WIDTH)
    columns = _find_columns(phrases, width, height) if phrases else None
    if columns is None:
        return extract_text(chars)
    x, top, bottom = columns

    def region(c) -> int:
        # Each character goes to exactly one region, by its centre.
        mid_y = (c["top"] + c["bottom"]) / 2
        if mid_y < top:
            return 0
        if mid_y > bottom:
            return 3
        return 1 if (c["x0"] + c["x1"]) / 2 < x else 2

    parts = [extract_text([c for c in chars if region(c) == r]) for r in range(4)]
    return "\n".join(p for p in parts if p)


def page_text(page) -> str:
    """Return a pdfplumber page's text in reading order."""
    texts = [_text(*group) for group in _orientations(page)]
    return "\n".join(t for t in texts if t)
