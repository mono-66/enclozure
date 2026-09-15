"""Build every box size, in four variants, into an output directory (default: dist).

Variants: {plain, flanged} x {heat-set insert holes, self-tapping screw holes}.
Each variant of each size becomes one zip (STEP + STL + PCB DXF + parameters).
Also builds a fitted example enclosure for every board preset that has connector
openings (Arduino / Raspberry Pi), so there is a ready-made example of what a box
with connector holes looks like -- same four variants each.
If run inside a tag build (GITHUB_REF_TYPE=tag), also writes dist/NOTES.md with a
wide download table (all variants of a size on one row) for the release body.
"""
import math
import os
import sys
from urllib.parse import quote

from box_sizes import SIZES
from enclosure import BOARDS, Enclosure, board_points

LID_HEIGHT = 10.0
INSERT_HOLE = 4.0   # M3 heat-set insert
SCREW_HOLE = 2.6    # M3 self-tapping screw pilot

# (suffix, options, column header)
VARIANTS = [
    ("_insert", dict(flange=False, outer_pillar_hole=INSERT_HOLE), "Insert"),
    ("_insert_flanged", dict(flange=True, outer_pillar_hole=INSERT_HOLE), "Insert + flange"),
    ("_screw", dict(flange=False, outer_pillar_hole=SCREW_HOLE), "Screw"),
    ("_screw_flanged", dict(flange=True, outer_pillar_hole=SCREW_HOLE), "Screw + flange"),
]

# Board presets that carry connector openings -- built as fitted examples.
CONNECTOR_BOARDS = sorted(name for name, b in BOARDS.items() if b.get("connectors"))


def _example_box_for_board(name):
    """A sensible example enclosure size (width, breadth, height in mm) for a
    connector board: the board footprint plus margin for the walls, seal and
    standoffs, and a base tall enough to clear the tallest connector.

    Sizing the box close to the board keeps the connector windows lined up with
    where the real board's ports sit."""
    b = BOARDS[name]
    bx, by = b["size"]
    margin = 18.0  # room for outer wall + seal + a corner standoff each side
    width = round(bx + margin, 1)
    breadth = round(by + margin, 1)
    tallest = max((c["h"] for c in b["connectors"]), default=0.0)
    # base floor (outer_wall) + standoff + board thickness + connector rise +
    # clearance + a little rim above the opening; rounded up to a tidy 5 mm.
    base_needed = 2.0 + 4.0 + 1.6 + tallest + 0.5 + 1.5
    base_height = max(20.0, 5.0 * math.ceil(base_needed / 5.0))
    height = base_height + LID_HEIGHT
    return width, breadth, height


def build(outdir: str = "dist"):
    os.makedirs(outdir, exist_ok=True)
    table = []  # [(length, width, height, {suffix: filename})]
    for length, width, height in SIZES:
        per_variant = {}
        for suffix, opts, _ in VARIANTS:
            enc = Enclosure(
                width=length,
                breadth=width,
                base_height=round(height - LID_HEIGHT, 1),
                lid_height=LID_HEIGHT,
                **opts,
            )
            zp = enc.export_zip(outdir, suffix=suffix)
            per_variant[suffix] = os.path.basename(zp)
            print("built", os.path.basename(zp))
        table.append((length, width, height, per_variant))
    board_table = build_boards(outdir)
    _write_notes(outdir, table, board_table)
    return table


def build_boards(outdir: str = "dist"):
    """Build one fitted example enclosure per connector board, in all four
    variants: standoffs on the board's mounting holes and connector windows cut
    in the walls."""
    rows = []  # [(board, desc, width, breadth, height, {suffix: filename})]
    for name in CONNECTOR_BOARDS:
        b = BOARDS[name]
        width, breadth, height = _example_box_for_board(name)
        pts = board_points(name)
        per_variant = {}
        for suffix, opts, _ in VARIANTS:
            enc = Enclosure(
                width=width,
                breadth=breadth,
                base_height=round(height - LID_HEIGHT, 1),
                lid_height=LID_HEIGHT,
                pcb_custom_points=pts,
                pcb_board=name,
                connectors=True,
                **opts,
            )
            enc.pcb_screw_hole = b["screw"]
            enc.m3_clearance = b["clearance"]
            zp = enc.export_zip(outdir, suffix="_" + name + suffix)
            per_variant[suffix] = os.path.basename(zp)
            print("built", os.path.basename(zp))
        rows.append((name, b["desc"], width, breadth, height, per_variant))
    return rows


def _write_notes(outdir, table, board_table=None):
    """Write a wide release-notes table: one row per size, a link per variant.
    A second table lists the fitted board examples (with connector holes)."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    tag = os.environ.get("GITHUB_REF_NAME") if os.environ.get("GITHUB_REF_TYPE") == "tag" else None
    if not (repo and tag):
        return

    def link(fn):
        return f"[zip](https://github.com/{repo}/releases/download/{tag}/{quote(fn)})"

    headers = [v[2] for v in VARIANTS]
    lines = [
        "## Pre-built boxes",
        "",
        "Each zip contains base + lid (STEP & STL), the PCB outline (DXF) and a parameters file.",
        "",
        "| Size L×W×H (mm) | " + " | ".join(headers) + " |",
        "|" + "---|" * (len(headers) + 1),
    ]
    for length, width, height, per_variant in table:
        cells = [link(per_variant[v[0]]) for v in VARIANTS]
        lines.append(f"| {length:g}×{width:g}×{height:g} | " + " | ".join(cells) + " |")

    if board_table:
        lines += [
            "",
            "## Board examples (with connector holes)",
            "",
            "Fitted example boxes: standoffs on the board's mounting holes and "
            "connector windows cut in the walls.",
            "",
            "| Board | Size L×W×H (mm) | " + " | ".join(headers) + " |",
            "|" + "---|" * (len(headers) + 2),
        ]
        for name, desc, width, breadth, height, per_variant in board_table:
            cells = [link(per_variant[v[0]]) for v in VARIANTS]
            lines.append(f"| {desc} | {width:g}×{breadth:g}×{height:g} | " + " | ".join(cells) + " |")

    with open(os.path.join(outdir, "NOTES.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("wrote", os.path.join(outdir, "NOTES.md"))


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "dist")
