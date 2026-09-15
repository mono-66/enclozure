"""Parametric sealed enclosure (CadQuery).

Run as a script to export STEP + STL of the parts, zipped, into the CWD:

    uv run enclosure.py --width 100 --breadth 80 --lid-height 10 --base-height 30

Open in CQ-editor to interactively inspect (it injects `show_object`); comment /
uncomment the calls at the very bottom to look at the base, lid or assembly.
"""
import math
from dataclasses import dataclass

import cadquery as cq


def _convex_hull(points):
    """2-D convex hull (Andrew's monotone chain), returned CCW."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


# ---------------------------------------------------------------------------
# Known board mounting-hole patterns
# ---------------------------------------------------------------------------
# Each entry gives the board outline size and the mounting-hole coordinates
# measured from the board's bottom-left corner (mm), plus the screw it takes.
# `screw` is the self-tapper pilot diameter for the standoff; `clearance` is the
# hole left in the exported PCB outline. `board_points()` re-references the holes
# to the board-outline centre so the board sits centred in the enclosure.
BOARDS = {
    # --- Arduino (M3) -----------------------------------------------------
    "arduino-uno": {
        "desc": "Arduino Uno R3 / Leonardo (68.6 x 53.3 mm)",
        "size": (68.58, 53.34),
        "holes": [(15.24, 2.54), (15.24, 50.8), (66.04, 7.62), (66.04, 35.56)],
        "screw": 2.5, "clearance": 3.2,
        # USB-B and barrel jack sit on the left short edge (x=0). Positions are
        # approximate (verify against your board; tune here or with --cutout).
        "connectors": [
            {"side": "-x", "pos": 40.9, "w": 13.0, "h": 11.0, "desc": "USB-B"},
            {"side": "-x", "pos": 8.9, "w": 9.5, "h": 11.0, "desc": "barrel jack"},
        ],
    },
    "arduino-mega": {
        # First four holes are the Uno/shield-compatible pattern, so they match
        # arduino-uno exactly; the last two are the extra holes on the long end.
        "desc": "Arduino Mega 2560 / Due (101.6 x 53.3 mm)",
        "size": (101.6, 53.34),
        "holes": [(15.24, 2.54), (15.24, 50.8), (66.04, 7.62), (66.04, 35.56),
                  (90.17, 2.54), (96.52, 50.8)],
        "screw": 2.5, "clearance": 3.2,
        # Same USB-B + barrel jack on the left short edge as the Uno (approximate).
        "connectors": [
            {"side": "-x", "pos": 40.9, "w": 13.0, "h": 11.0, "desc": "USB-B"},
            {"side": "-x", "pos": 8.9, "w": 9.5, "h": 11.0, "desc": "barrel jack"},
        ],
    },
    # --- Raspberry Pi (M2.5) ---------------------------------------------
    "rpi-b": {
        "desc": "Raspberry Pi B+/2/3/4/5 full-size (85 x 56 mm)",
        "size": (85.0, 56.0),
        "holes": [(3.5, 3.5), (3.5, 52.5), (61.5, 3.5), (61.5, 52.5)],
        "screw": 2.1, "clearance": 2.7,
        # Connector layout matches the Pi 4 / Pi 5 (per the Pi 4B datasheet
        # mechanical drawing). The mounting holes are shared with B+/2/3, but
        # those older boards have a different port layout on these edges.
        "connectors": [
            {"side": "-y", "pos": 11.2, "w": 9.0, "h": 3.5, "desc": "USB-C power"},
            {"side": "-y", "pos": 26.0, "w": 8.0, "h": 6.0, "desc": "micro-HDMI 0"},
            {"side": "-y", "pos": 39.5, "w": 8.0, "h": 6.0, "desc": "micro-HDMI 1"},
            {"side": "-y", "pos": 53.5, "w": 7.0, "h": 6.0, "desc": "A/V jack"},
            {"side": "+x", "pos": 45.75, "w": 16.0, "h": 13.5, "desc": "Ethernet"},
            {"side": "+x", "pos": 27.0, "w": 15.0, "h": 16.0, "desc": "USB 3.0"},
            {"side": "+x", "pos": 9.0, "w": 15.0, "h": 16.0, "desc": "USB 2.0"},
        ],
    },
    "rpi-a": {
        "desc": "Raspberry Pi 3 A+ (65 x 56 mm)",
        "size": (65.0, 56.0),
        "holes": [(3.5, 3.5), (3.5, 52.5), (61.5, 3.5), (61.5, 52.5)],
        "screw": 2.1, "clearance": 2.7,
    },
    "rpi-zero": {
        "desc": "Raspberry Pi Zero / Zero W / Zero 2 W (65 x 30 mm)",
        "size": (65.0, 30.0),
        "holes": [(3.5, 3.5), (3.5, 26.5), (61.5, 3.5), (61.5, 26.5)],
        "screw": 2.1, "clearance": 2.7,
    },
    # --- Raspberry Pi Pico (M2) ------------------------------------------
    "rpi-pico": {
        "desc": "Raspberry Pi Pico / Pico W (51 x 21 mm)",
        "size": (51.0, 21.0),
        "holes": [(2.0, 4.8), (2.0, 16.2), (49.0, 4.8), (49.0, 16.2)],
        "screw": 1.7, "clearance": 2.4,
    },
}


def board_points(name, offset=(0.0, 0.0)):
    """Mounting-hole (x, y) for a named board, referenced to the board-outline
    centre (so the board sits centred in the box). `offset` shifts the pattern."""
    b = BOARDS[name]
    cx, cy = b["size"][0] / 2.0, b["size"][1] / 2.0
    ox, oy = offset
    return [(x - cx + ox, y - cy + oy) for (x, y) in b["holes"]]


@dataclass
class Enclosure:
    # ---- overall size (the four headline parameters) --------------------
    width: float = 100.0       # X
    breadth: float = 80.0      # Y
    lid_height: float = 10.0   # Z
    base_height: float = 30.0  # Z

    # ---- shell / seal ---------------------------------------------------
    outer_wall: float = 2.0
    oring_notional: float = 2.0    # o-ring cord (cross-section) diameter
    oring_compression: float = 0.20  # fraction the o-ring is squashed when closed
    ledge: float = 1.2             # ridge (tongue) width
    ledge_height: float = 0.6      # ridge height (left fixed; groove depth follows compression)

    # ---- corner screw posts --------------------------------------------
    outer_pillar_hole: float = 4.4              # threaded-insert hole (base)
    outer_pillar_lid_clearance_hole: float = 3.6  # screw clearance (lid)

    # Outer corner rounding. None -> the minimum that still buries the corner
    # screw post in the wall (the old behaviour). Larger values give a more
    # rounded box; the screw posts slide inward along the diagonal to stay
    # nestled in the corner. Clamped to a safe range by `corner_r`.
    corner_radius: float = None
    # Seal routing. "cross" (default): the original plus/cross path that routes
    # inboard of each corner post -- the seal the box shipped with. "round": one
    # clean rounded-rectangle seal with the posts in the solid corner pockets
    # outside it (drawing style, opt-in).
    seal_style: str = "cross"
    # Seal corner radius override, in mm. None -> automatic: "cross" uses the
    # original shoulder-limited fillet; "round" uses the smallest radius whose
    # corner pocket fits the screw post. Clamped to buildable bounds either way.
    seal_corner_radius: float = None

    # ---- M5 wall-mount flange (opt-in) ---------------------------------
    flange: bool = False
    flange_thickness: float = 4.0
    flange_wall_gap: float = 1.0        # gap from case wall to the head hole
    flange_end_margin: float = 4.0      # keep end round holes in from the corners
    keyhole_len_heads: float = 2.0      # slot length (eye centre -> slot end) in head widths
    m5_clearance: float = 5.5           # M5 shank clearance
    m5_head_clearance: float = 10.0     # M5 head pass-through (keyhole eye / "head width")

    # ---- PCB mounting standoffs (on by default) ------------------------
    pcb_mounts: bool = True
    pcb_screw_hole: float = 2.5         # M3 self-tapper pilot hole
    pcb_pillar_height: float = 4.0      # fixed height above the inner surface
    pcb_wall_clearance: float = 4.0     # gap from the post edge to the inner wall, on the diagonal
    pcb_edge_clearance: float = 1.0     # PCB outline clearance inside the cavity wall
    m3_clearance: float = 3.2           # M3 clearance hole in the PCB

    # ---- PCB custom standoff positions (optional) -----------------------
    # List of (x, y) positions in mm from centre. If set, overrides auto placement.
    pcb_custom_points: list = None
    pcb_board: str = None  # name of a BOARDS preset, for reporting only

    # ---- connector openings in the base walls ---------------------------
    connectors: bool = True             # cut the selected board's connector openings
    pcb_board_thickness: float = 1.6    # PCB thickness (sets the connector height datum)
    pcb_offset: tuple = (0.0, 0.0)      # board shift from centre (matches pcb_points)
    connector_clearance: float = 0.5    # slack added around every connector opening
    # Extra openings independent of the board, each (side, pos, width, height) in
    # ENCLOSURE coords: side in {+x,-x,+y,-y}, pos = centre along that wall.
    pcb_extra_cutouts: list = None

    # ---- misc -----------------------------------------------------------
    assembly_gap: float = 0.0

    # =====================================================================
    # Derived seal dimensions
    # =====================================================================
    @property
    def oring_notch(self) -> float:
        return self.oring_notional + 0.8

    @property
    def lid_groove_depth(self) -> float:
        # Once the ridge is seated, the o-ring is squeezed into (depth - ridge),
        # so depth = compressed cord + ridge height gives the target compression.
        return self.oring_notional * (1.0 - self.oring_compression) + self.ledge_height

    @property
    def ledge_from_outer(self) -> float:
        # centre the ledge in the o-ring notch
        return self.outer_wall + self.oring_notch / 2 - self.ledge / 2

    @property
    def outer_pillar_dia(self) -> float:
        return self.outer_pillar_hole + 2 * self.outer_wall

    @property
    def corner_r(self) -> float:
        """Outer-wall corner fillet radius, clamped to a buildable range.

        Floor: outer_pillar_dia/2, the tightest corner that still fully wraps a
        screw post sitting hard in the corner. Ceiling: just under half the
        short side, so opposite corner arcs never overlap."""
        floor = self.outer_pillar_dia / 2
        ceil = min(self.width, self.breadth) / 2 - self.outer_wall
        # Default: 10% of the short side -> a clearly rounded box that scales
        # with size. The legacy cross seal keeps its original tight corner
        # (its plus/cross path was drawn for posts hard in the corner).
        if self.seal_style == "cross":
            default = floor
        else:
            default = max(floor, 0.10 * min(self.width, self.breadth))
        r = default if self.corner_radius is None else self.corner_radius
        return max(floor, min(r, ceil))

    @property
    def seal_inset(self) -> float:
        """Seal centreline distance in from the outer face."""
        return self.outer_wall + self.oring_notch / 2

    @property
    def _round_seal_min_radius(self) -> float:
        # Everything lives on the corner diagonal. The seal corner arc's
        # nearest point to the corner sits at depth (inset + rs)*sqrt2 - rs;
        # the post centre (tangent inside the outer arc) at R*(sqrt2-1) +
        # post_r. Their gap must cover half the o-ring notch, a web of
        # material, and the screw-hole radius. Solve for rs.
        s2 = math.sqrt(2)
        post_r = self.outer_pillar_dia / 2
        clr = self.oring_notch / 2 + self.outer_pillar_hole / 2 + 1.2
        return (clr + post_r + self.corner_r * (s2 - 1) - self.seal_inset * s2) / (s2 - 1)

    @property
    def oring_centre_radius(self) -> float:
        """Corner radius of the seal path (both styles), clamped buildable."""
        if self.seal_style == "round":
            floor = self._round_seal_min_radius
            ceil = min(self.width, self.breadth) / 2 - self.seal_inset - 1.0
            assert floor <= ceil, (
                f"box too small for a round seal: needs corner radius {floor:.1f} "
                f"but only {ceil:.1f} fits; use seal_style='cross' or a bigger box")
            r = floor if self.seal_corner_radius is None else self.seal_corner_radius
            return max(floor, min(r, ceil))
        # cross (original): the plus/cross has a convex arm tip a short
        # "shoulder" from a concave armpit; two equal fillets need 2*r of
        # straight edge between them, so r <= shoulder/2 (small margin OCC
        # likes). This is the exact seal the box shipped with.
        if self.seal_corner_radius is not None:
            return max(0.5, min(self.seal_corner_radius, 5.0))
        seal_shoulder = self.outer_pillar_dia - self.ledge_from_outer
        r = min(4.0, seal_shoulder / 2.0 - 0.1)
        assert r > 0.0, "shoulder too small for any fillet"
        return r

    # =====================================================================
    # Seal geometry
    # =====================================================================
    def sketch_outside(self) -> cq.Sketch:
        return cq.Sketch().rect(self.width, self.breadth).vertices().fillet(self.corner_r)

    def seal_centreline(self) -> cq.Sketch:
        """Seal path centreline.

        round: one clean rounded rectangle a constant seal_inset from the wall;
        the generous corner radius leaves a solid pocket at each corner, outside
        the sealed volume, that the screw post sits in.
        cross: legacy plus/cross routed inboard of the corner posts."""
        if self.seal_style == "round":
            i2 = 2 * self.seal_inset
            return (
                cq.Sketch()
                .rect(self.width - i2, self.breadth - i2)
                .vertices()
                .fillet(self.oring_centre_radius)
            )
        return (
            cq.Sketch()
            .rect(self.width - 2 * self.outer_pillar_dia, self.breadth - 2 * self.ledge_from_outer)
            .rect(self.width - 2 * self.ledge_from_outer, self.breadth - 2 * self.outer_pillar_dia)
            .clean()
            .vertices()
            .fillet(self.oring_centre_radius)
        )

    def centre_wire(self) -> cq.Wire:
        """Outer wire of the centreline (cached). `.reset()` clears the vertex
        selection left by `.fillet()` so `.faces()` can see the face again."""
        if getattr(self, "_cw", None) is None:
            self._cw = self.seal_centreline().reset().faces().val().outerWire()
        return self._cw

    @property
    def oring_count(self) -> int:
        """How many separate o-ring seals the box needs: one continuous cord
        per closed seal loop (the plus/cross seal is a single loop, so 1)."""
        return len(self.seal_centreline().reset().faces().vals())

    @property
    def oring_length(self) -> float:
        """Cord length to cut for each o-ring: the perimeter of the seal
        centreline the cord cross-section is centred on."""
        return self.centre_wire().Length()

    def cross_prism(self, half_width: float, height: float) -> cq.Workplane:
        """Prism whose footprint is the centreline offset sideways by half_width
        (positive = outward, negative = inward). "arc" rounds the convex corners."""
        wire = self.centre_wire().offset2D(half_width, "arc")[0]
        face = cq.Face.makeFromWires(wire)
        solid = cq.Solid.extrudeLinear(face, cq.Vector(0, 0, height))
        return cq.Workplane(obj=solid)

    # =====================================================================
    # Corner screw bores
    # =====================================================================
    def screw_points(self):
        # Sit each post in the rounded corner, tangent to the outer arc: its
        # centre rides the 45-deg diagonal at (corner_r - post_r) from the arc
        # centre. When corner_r == post_r this reduces to the old hard-corner
        # placement; larger radii slide the post inward so it stays buried.
        pr = self.outer_pillar_dia / 2
        off = (self.corner_r - pr) / math.sqrt(2)
        dx = self.width / 2 - self.corner_r + off
        dy = self.breadth / 2 - self.corner_r + off
        return [(dx, dy), (-dx, dy), (dx, -dy), (-dx, -dy)]

    def drill_corners(self, part: cq.Workplane, dia: float, z0: float, z1: float) -> cq.Workplane:
        r = dia / 2.0
        for (x, y) in self.screw_points():
            bore = cq.Solid.makeCylinder(r, z1 - z0, cq.Vector(x, y, z0), cq.Vector(0, 0, 1))
            part = part.cut(cq.Workplane(obj=bore))
        return part

    # =====================================================================
    # Flange
    # =====================================================================
    def _flange_geom(self):
        """Shared flange dimensions.

        Features live just outside each long (X) wall, so the keyhole slot runs
        parallel to that wall. pad_r is the screw-head pad radius (head + clear).
        """
        head_r = self.m5_head_clearance / 2
        shank_r = self.m5_clearance / 2
        # hull disc radius: leaves one wall thickness of plate around the head hole
        pad_r = head_r + self.outer_wall
        slot_len = self.keyhole_len_heads * self.m5_head_clearance  # eye centre -> slot end
        feat_y = self.breadth / 2 + head_r + self.flange_wall_gap   # the head hole clears the wall
        round_x = self.width / 2 - (pad_r + self.flange_end_margin)
        # keep the end round holes only while their pads clear the centre slot pad
        include_round = (round_x - pad_r) > (slot_len / 2 + pad_r + 2.0)
        return head_r, shank_r, pad_r, slot_len, feat_y, round_x, include_round

    def include_round_holes(self) -> bool:
        return self._flange_geom()[6]

    def _flange_centres(self):
        """(x, y, radius) of every disc the flange hulls: the corner pillars
        (at the pillar radius, so the hull reproduces the case outline) plus a
        head-clearance disc at each mounting hole / slot end."""
        head_r, shank_r, pad_r, slot_len, feat_y, round_x, inc = self._flange_geom()
        discs = [(x, y, self.outer_pillar_dia / 2) for (x, y) in self.screw_points()]
        for sgn in (+1, -1):
            fy = sgn * feat_y
            discs += [(-slot_len / 2, fy, pad_r), (slot_len / 2, fy, pad_r)]  # slot ends
            if inc:
                discs += [(round_x, fy, pad_r), (-round_x, fy, pad_r)]
        return discs

    def _flange_outline(self, segments: int = 48):
        """Convex hull of one disc per centre -> a smooth, all-convex base with
        no concave (crack-prone) features. Each disc is sampled into points and
        the hull of the lot is taken."""
        pts = []
        for (x, y, r) in self._flange_centres():
            for k in range(segments):
                a = 2 * math.pi * k / segments
                pts.append((x + r * math.cos(a), y + r * math.sin(a)))
        return _convex_hull(pts)

    def make_flanges(self) -> cq.Workplane:
        return cq.Workplane("XY").polyline(self._flange_outline()).close().extrude(self.flange_thickness)

    def cut_flange_holes(self, part: cq.Workplane) -> cq.Workplane:
        head_r, shank_r, pad_r, slot_len, feat_y, round_x, inc = self._flange_geom()
        t = self.flange_thickness
        for sgn in (+1, -1):
            fy = sgn * feat_y
            # Keyhole: eye (head passes) at +X end, shank slot back to -X end.
            sk = (
                cq.Sketch()
                .push([(slot_len / 2, fy)]).circle(head_r)
                .reset().push([(0.0, fy)]).rect(slot_len, 2 * shank_r)
                .reset().push([(-slot_len / 2, fy)]).circle(shank_r)
                .clean()
            )
            keyhole = cq.Workplane("XY").placeSketch(sk).extrude(t + 2).translate((0, 0, -1))
            part = part.cut(keyhole)
            if inc:
                for cx in (round_x, -round_x):
                    cyl = cq.Solid.makeCylinder(shank_r, t + 2, cq.Vector(cx, fy, -1))
                    part = part.cut(cq.Workplane(obj=cyl))
        return part

    # =====================================================================
    # PCB mounting standoffs
    # =====================================================================
    @property
    def pcb_pillar_dia(self) -> float:
        return self.pcb_screw_hole + 2 * self.outer_wall

    def pcb_points(self):
        """Standoff centres on the diagonals, the post edge held
        pcb_wall_clearance from the inner wall. The cavity corners are filleted,
        so the position is solved numerically against the real wall. Four on big
        boxes, dropping to a diagonal pair, then a single central post.
        If pcb_custom_points is set, those (x, y) positions are used directly."""
        if getattr(self, "_pts", None) is not None:
            return self._pts
        if self.pcb_custom_points is not None:
            self._pts = list(self.pcb_custom_points)
            return self._pts

        Rpp = self.pcb_pillar_dia / 2
        target = Rpp + self.pcb_wall_clearance       # post centre -> wall distance
        a1 = self.width / 2 - self.outer_pillar_dia  # centreline armpit, +,+ quadrant
        b2 = self.breadth / 2 - self.outer_pillar_dia
        wall = self.centre_wire().offset2D(-self.ledge / 2, "arc")[0]

        def clearance_at(s):  # move inward along the diagonal from the armpit
            return wall.distance(cq.Vertex.makeVertex(a1 - s, b2 - s, 0))

        lo, hi = 0.0, max(a1, b2)
        for _ in range(32):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if clearance_at(mid) < target else (lo, mid)
        s = (lo + hi) / 2
        px, py = a1 - s, b2 - s

        sep = self.pcb_pillar_dia + 1.0  # keep posts off each other
        if px > 0 and py > 0 and 2 * px >= sep and 2 * py >= sep:
            self._pts = [(px, py), (-px, py), (px, -py), (-px, -py)]
        elif px > 0 and py > 0 and 2 * math.hypot(px, py) >= sep:
            self._pts = [(px, py), (-px, -py)]
        else:
            self._pts = [(0.0, 0.0)]
        return self._pts

    def pcb_fit_problems(self):
        """Standoff centres that don't fit the base cavity: either outside the
        inner wall or so close that the pillar would clash with it. Returns the
        offending (x, y) list (empty == all good). Best-effort; never raises."""
        try:
            wire = self.centre_wire().offset2D(-self.ledge / 2, "arc")[0]
            face = cq.Face.makeFromWires(wire)
            r = self.pcb_pillar_dia / 2.0
            bad = []
            for (x, y) in self.pcb_points():
                v = cq.Vertex.makeVertex(x, y, 0)
                inside = face.distance(v) < 1e-6
                if not inside or wire.distance(v) < r:
                    bad.append((x, y))
            return bad
        except Exception:
            return []

    def _add_standoffs(self, part: cq.Workplane, floor_z: float) -> cq.Workplane:
        """Add fixed-height self-tapper pillars standing on the inner surface."""
        h = self.pcb_pillar_height
        r = self.pcb_pillar_dia / 2
        hr = self.pcb_screw_hole / 2
        for (x, y) in self.pcb_points():
            pillar = cq.Solid.makeCylinder(r, h, cq.Vector(x, y, floor_z))
            part = part.union(cq.Workplane(obj=pillar))
            bore = cq.Solid.makeCylinder(hr, h, cq.Vector(x, y, floor_z))  # blind to the floor
            part = part.cut(cq.Workplane(obj=bore))
        return part

    def pcb_sketch(self, part: str = "base") -> cq.Sketch:
        """PCB outline derived from the seal centreline, offset inward to clear
        that part's cavity wall by pcb_edge_clearance, with an M3 clearance hole
        at every standoff. The base and lid cavities differ (the lid wall sits
        further in), so each part gets its own outline."""
        wall = (self.oring_notional / 2 + self.outer_wall) if part == "lid" else (self.ledge / 2)
        off = wall + self.pcb_edge_clearance
        wire = self.centre_wire().offset2D(-off, "arc")[0]
        sk = cq.Sketch().face(wire)
        for (x, y) in self.pcb_points():
            sk = sk.push([(x, y)]).circle(self.m3_clearance / 2, mode="s").reset()
        return sk.clean()

    # =====================================================================
    # Connector openings (base walls)
    # =====================================================================
    @property
    def pcb_top_z(self) -> float:
        """Z of the PCB top surface: cavity floor + standoff + board thickness.
        Connector heights are measured up from here."""
        return self.outer_wall + self.pcb_pillar_height + self.pcb_board_thickness

    def _resolved_connectors(self):
        """All openings to cut, resolved to enclosure coords: a list of
        (side, centre, width, height, desc). `centre` is the position along the
        wall; `height` is the opening's rise above the PCB top surface. Board
        connectors are mapped from board coords; `pcb_extra_cutouts` are already
        in enclosure coords."""
        out = []
        if self.connectors and self.pcb_board:
            b = BOARDS.get(self.pcb_board, {})
            (cx, cy) = (b.get("size", (0.0, 0.0))[0] / 2.0,
                        b.get("size", (0.0, 0.0))[1] / 2.0)
            ox, oy = self.pcb_offset
            for c in b.get("connectors", []):
                side = c["side"]
                centre = (c["pos"] - cx + ox) if side in ("+y", "-y") else (c["pos"] - cy + oy)
                out.append((side, centre, c["w"], c["h"], c.get("desc", "")))
        for cut in (self.pcb_extra_cutouts or []):
            side, pos, w, h = cut[0], cut[1], cut[2], cut[3]
            out.append((side, pos, w, h, "custom"))
        return out

    def _cut_connectors(self, part: cq.Workplane) -> cq.Workplane:
        """Cut a rectangular window through the base wall for each connector,
        sized with clearance and clamped so it never breaks the seal rim."""
        conns = self._resolved_connectors()
        if not conns:
            return part
        clr = self.connector_clearance
        z_cap = self.base_height - 0.5          # leave the rim (seal) intact
        pierce = self.seal_inset + self.oring_notch + 3.0  # depth to clear the wall
        warned = []
        for (side, c, w, h, desc) in conns:
            z0 = max(0.6, self.pcb_top_z - clr)
            z1_nom = self.pcb_top_z + h + clr
            z1 = min(z1_nom, z_cap)
            if z1 <= z0:
                warned.append(f"{desc or side} (no vertical room, skipped)")
                continue
            if z1_nom > z_cap + 1e-6:
                warned.append(f"{desc or side} (height clipped to fit the box)")
            half = w / 2.0 + clr
            if side in ("+y", "-y"):
                x0, dx = c - half, w + 2 * clr
                y0 = (-self.breadth / 2 - 1.0) if side == "-y" else (self.breadth / 2 - pierce)
                box = cq.Solid.makeBox(dx, pierce + 1.0, z1 - z0, cq.Vector(x0, y0, z0))
            elif side in ("+x", "-x"):
                y0, dy = c - half, w + 2 * clr
                x0 = (-self.width / 2 - 1.0) if side == "-x" else (self.width / 2 - pierce)
                box = cq.Solid.makeBox(pierce + 1.0, dy, z1 - z0, cq.Vector(x0, y0, z0))
            else:
                warned.append(f"{desc or side} (unknown side '{side}', skipped)")
                continue
            part = part.cut(cq.Workplane(obj=box))
        if warned:
            print("WARNING: connector openings adjusted: " + "; ".join(warned)
                  + "\n         raise --base-height for full-height connector cutouts.")
        return part

    # =====================================================================
    # Parts
    # =====================================================================
    def make_base(self) -> cq.Workplane:
        """Box, seal ridge, plus-shaped cavity, optional flange, through bores."""
        cavity_floor = self.outer_wall
        cavity_depth = self.base_height + self.ledge_height - cavity_floor

        base = cq.Workplane("XY").placeSketch(self.sketch_outside()).extrude(self.base_height)

        ridge = self.cross_prism(self.ledge / 2, self.ledge_height).cut(
            self.cross_prism(-self.ledge / 2, self.ledge_height)
        )
        base = base.union(ridge.translate((0, 0, self.base_height)))

        # Flange first: it is a full-footprint plate, so the cavity must be cut
        # AFTER it — otherwise it back-fills the cavity floor (making it 4 mm
        # thick) and buries the lower half of the standoff bores.
        if self.flange:
            base = base.union(self.make_flanges())

        cavity = self.cross_prism(-self.ledge / 2, cavity_depth + 1.0).translate((0, 0, cavity_floor))
        base = base.cut(cavity)

        if self.pcb_mounts:
            base = self._add_standoffs(base, cavity_floor)

        # Threaded-insert bores: constant diameter, straight through (no support).
        base = self.drill_corners(base, self.outer_pillar_hole, -1.0, self.base_height + self.ledge_height + 1.0)

        if self.flange:
            base = self.cut_flange_holes(base)

        base = self._cut_connectors(base)
        return base

    def make_lid(self) -> cq.Workplane:
        """Lid (built the same way up as the base for easy comparison).

        Across the seal, outward -> inward: outer wall | o-ring groove (cut from
        the rim) | extra inner wall (one wall thick) | main cavity. The groove's
        inner edge sits at the seal centreline - oring_notional/2.
        """
        half = self.oring_notional / 2.0
        lid_floor = self.outer_wall

        lid = cq.Workplane("XY").placeSketch(self.sketch_outside()).extrude(self.lid_height)

        inner_edge = -(half + self.outer_wall)
        cav_depth = self.lid_height - lid_floor
        cavity = self.cross_prism(inner_edge, cav_depth + 1.0).translate((0, 0, lid_floor))
        lid = lid.cut(cavity)

        groove = self.cross_prism(half, self.lid_groove_depth + 1.0).cut(
            self.cross_prism(-half, self.lid_groove_depth + 1.0)
        )
        groove = groove.translate((0, 0, self.lid_height - self.lid_groove_depth))
        lid = lid.cut(groove)

        if self.pcb_mounts:
            lid = self._add_standoffs(lid, lid_floor)

        lid = self.drill_corners(lid, self.outer_pillar_lid_clearance_hole, -1.0, self.lid_height + 1.0)
        return lid

    def make_assembly(self, gap: float = None) -> cq.Assembly:
        if gap is None:
            gap = self.assembly_gap
        base = self.make_base()
        lid = self.make_lid().rotate((0, 0, 0), (1, 0, 0), 180)
        lid = lid.translate((0, 0, self.base_height + self.lid_height + gap))
        asm = cq.Assembly()
        asm.add(base, name="base", color=cq.Color(0.85, 0.72, 0.18))
        asm.add(lid, name="lid", color=cq.Color(0.30, 0.55, 0.80))
        return asm

    # =====================================================================
    # Export
    # =====================================================================
    def stem(self) -> str:
        return (f"enclosure_{self.width:g}x{self.breadth:g}"
                f"_base{self.base_height:g}_lid{self.lid_height:g}")

    def params_text(self) -> str:
        """Human-readable dump of the inputs and the derived/generated values."""
        pts = self.pcb_points()
        if self.pcb_board:
            # Asymmetric board patterns: report the hole bounding box instead of
            # a mirrored spacing, which only makes sense for the auto layout.
            xs2 = [x for x, _ in pts]
            ys2 = [y for _, y in pts]
            sx = f"{max(xs2) - min(xs2):g}"
            sy = f"{max(ys2) - min(ys2):g}"
        else:
            xs = sorted({round(abs(x), 2) for x, _ in pts if abs(x) > 1e-6})
            ys = sorted({round(abs(y), 2) for _, y in pts if abs(y) > 1e-6})
            sx = f"{2 * xs[0]:g}" if xs else "-"
            sy = f"{2 * ys[0]:g}" if ys else "-"
        lines = [
            "Enclosure - parameters",
            "================================",
            "INPUTS",
            f"  width x breadth      : {self.width:g} x {self.breadth:g} mm",
            f"  base_height          : {self.base_height:g} mm",
            f"  lid_height           : {self.lid_height:g} mm",
            f"  outer_wall           : {self.outer_wall:g} mm",
            f"  flange               : {self.flange}",
            f"  pcb_mounts           : {self.pcb_mounts}",
            "",
            "GENERATED",
            f"  seal style           : {self.seal_style}",
            f"  outer corner radius  : {self.corner_r:g} mm",
            f"  seal corner radius   : {self.oring_centre_radius:g} mm",
            f"  corner pillar dia    : {self.outer_pillar_dia:g} mm (base hole {self.outer_pillar_hole:g})",
            f"  lid clearance hole   : {self.outer_pillar_lid_clearance_hole:g} mm",
            f"  o-ring cord          : {self.oring_notional:g} mm @ {self.oring_compression * 100:g}% compression",
            f"  ridge / groove depth : {self.ledge_height:g} / {self.lid_groove_depth:g} mm",
            f"  o-ring centre radius : {self.oring_centre_radius:g} mm",
            f"  o-ring seals needed  : {self.oring_count} x {self.oring_length:g} mm cord",
            f"  PCB board            : {BOARDS[self.pcb_board]['desc'] if self.pcb_board else 'auto layout'}",
            f"  PCB standoffs        : {len(pts)} (self-tap pilot {self.pcb_screw_hole:g}, "
            f"pillar dia {self.pcb_pillar_dia:g}, height {self.pcb_pillar_height:g})",
            f"  PCB hole spacing     : {sx} x {sy} mm",
            f"  PCB clearance hole   : {self.m3_clearance:g} mm",
        ]
        conns = self._resolved_connectors()
        if conns:
            names = ", ".join(d or s for (s, _c, _w, _h, d) in conns)
            lines += [
                f"  connector openings   : {len(conns)} in base walls ({names})",
                f"  PCB top surface      : {self.pcb_top_z:g} mm above the base floor "
                f"(standoff {self.pcb_pillar_height:g} + board {self.pcb_board_thickness:g})",
            ]
        if self.flange:
            _hr, _sr, _pr, slot_len, _fy, _rx, inc = self._flange_geom()
            lines += [
                f"  flange thickness     : {self.flange_thickness:g} mm",
                f"  flange head hole     : {self.m5_head_clearance:g} mm (M5 head)",
                f"  flange shank slot    : {self.m5_clearance:g} mm wide, {slot_len:g} mm long (eye->end)",
                f"  flange round holes   : {'yes' if inc else 'no'}",
            ]
        return "\n".join(lines) + "\n"

    def export_zip(self, outdir: str = ".", suffix: str = "") -> str:
        """Write STEP + STL of base and lid into a single .zip in `outdir`.

        `suffix` is appended to the file stem (used to label build variants)."""
        import os
        import tempfile
        import zipfile

        os.makedirs(outdir, exist_ok=True)
        parts = {"base": self.make_base(), "lid": self.make_lid()}
        stem = self.stem() + suffix
        zip_path = os.path.join(outdir, stem + ".zip")

        with tempfile.TemporaryDirectory() as td:
            files = []
            for name, part in parts.items():
                step = os.path.join(td, f"{stem}_{name}.step")
                stl = os.path.join(td, f"{stem}_{name}.stl")
                cq.exporters.export(part, step)
                cq.exporters.export(part, stl, tolerance=0.1, angularTolerance=0.2)
                files += [step, stl]

            # PCB outlines (DXF) — one per part (base & lid cavities differ)
            for part in ("base", "lid"):
                dxf = os.path.join(td, f"{stem}_pcb_{part}.dxf")
                cq.exporters.export(cq.Workplane("XY").placeSketch(self.pcb_sketch(part)), dxf)
                files.append(dxf)

            # Parameters (text)
            txt = os.path.join(td, f"{stem}_parameters.txt")
            with open(txt, "w", encoding="utf-8") as fh:
                fh.write(self.params_text())
            files.append(txt)

            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
                for f in files:
                    z.write(f, os.path.basename(f))
        return zip_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    import argparse

    p = argparse.ArgumentParser(description="Export STEP + STL of the enclosure parts as a .zip in the CWD.")
    p.add_argument("--width", type=float, default=100.0, help="overall X (mm)")
    p.add_argument("--breadth", type=float, default=80.0, help="overall Y (mm)")
    p.add_argument("--lid-height", type=float, default=10.0, help="lid Z (mm)")
    p.add_argument("--base-height", type=float, default=30.0, help="base Z (mm)")
    p.add_argument("--oring-compression", type=float, default=0.20,
                   help="fraction the o-ring cord is squashed when the lid is closed "
                        "(e.g. 0.25 = 25%%; typical range 0.15-0.30; default: 0.20)")
    p.add_argument("--corner-radius", type=float, default=None,
                   help="outer-wall corner fillet in mm; bigger = more rounded box "
                        "(default: 10%% of the short side; clamped to a buildable range)")
    p.add_argument("--seal-style", choices=("cross", "round"), default="cross",
                   help="seal path: 'cross' = the original seal that routes inboard of "
                        "each corner post (default, back to normal); 'round' = one clean "
                        "rounded-rectangle seal with the posts in the corner pockets "
                        "outside it")
    p.add_argument("--seal-corner-radius", type=float, default=None,
                   help="seal corner radius in mm (default: automatic per style; "
                        "clamped to a buildable range)")
    p.add_argument("--flange", action="store_true", help="add the M5 wall-mount flange")
    p.add_argument("--no-pcb-mounts", dest="pcb_mounts", action="store_false",
                   help="omit the PCB standoffs (on by default)")
    p.add_argument("--pcb-pos", type=float, nargs=2, metavar=("X", "Y"),
                   help="standoff distance from centre in mm, e.g. --pcb-pos 30 25; "
                        "mirrors automatically to all four corners")
    p.add_argument("--board", choices=sorted(BOARDS),
                   help="place standoffs for a known board (Arduino / Raspberry Pi); "
                        "sets the hole positions and screw size. See --list-boards.")
    p.add_argument("--pcb-offset", type=float, nargs=2, metavar=("X", "Y"), default=(0.0, 0.0),
                   help="shift the --board pattern from centre by (X, Y) mm (default: 0 0)")
    p.add_argument("--pcb-hole", type=float, default=None,
                   help="standoff pilot-hole diameter in mm, for the screws that hold the "
                        "PCB down (default: 2.5 for M3 self-tappers; --board presets set "
                        "it per board, e.g. 2.1 for Raspberry Pi M2.5 / 1.7 for Pico M2; "
                        "this flag overrides either). The standoff pillar grows/shrinks "
                        "to suit.")
    p.add_argument("--no-connectors", dest="connectors", action="store_false",
                   help="omit the board's connector openings in the base walls "
                        "(cut by default when --board is set)")
    p.add_argument("--cutout", action="append", nargs=4, default=[],
                   metavar=("SIDE", "POS", "WIDTH", "HEIGHT"),
                   help="extra rectangular opening in a base wall. SIDE is one of "
                        "+x/-x/+y/-y; POS is the centre along that wall from the box "
                        "centre (mm); WIDTH is the opening size along the wall; HEIGHT "
                        "is its rise above the PCB top. Repeatable.")
    p.add_argument("--list-boards", action="store_true",
                   help="list the supported board presets and exit")
    p.add_argument("-o", "--outdir", default=".", help="output directory (default: CWD)")
    a = p.parse_args(argv)

    if a.list_boards:
        width = max(len(n) for n in BOARDS)
        print("Supported boards (use with --board):")
        for name in sorted(BOARDS):
            b = BOARDS[name]
            nconn = len(b.get("connectors", []))
            conn = f", {nconn} connector cutouts" if nconn else ""
            print(f"  {name:<{width}}  {b['desc']}  "
                  f"[{len(b['holes'])} holes, pilot {b['screw']:g} mm{conn}]")
        return

    if a.board and a.pcb_pos is not None:
        p.error("use either --board or --pcb-pos, not both")

    # Parse any --cutout requests: (side, pos, width, height) in enclosure coords.
    sides = ("+x", "-x", "+y", "-y")
    extra_cutouts = []
    for (side, pos, w, h) in a.cutout:
        if side not in sides:
            p.error(f"--cutout SIDE must be one of {', '.join(sides)} (got '{side}')")
        try:
            extra_cutouts.append((side, float(pos), float(w), float(h)))
        except ValueError:
            p.error("--cutout POS/WIDTH/HEIGHT must be numbers")

    if not 0.0 <= a.oring_compression < 1.0:
        p.error("--oring-compression must be in [0.0, 1.0)")

    # Resolve the standoff positions: a known board, a mirrored single position,
    # or None (auto layout).
    custom_points = None
    board_screw = board_clearance = None
    if a.board is not None:
        custom_points = board_points(a.board, tuple(a.pcb_offset))
        board_screw = BOARDS[a.board]["screw"]
        board_clearance = BOARDS[a.board]["clearance"]
    elif a.pcb_pos is not None:
        px, py = a.pcb_pos
        custom_points = [(px, py), (-px, py), (px, -py), (-px, -py)]

    enc = Enclosure(
        width=a.width, breadth=a.breadth,
        lid_height=a.lid_height, base_height=a.base_height,
        flange=a.flange, pcb_mounts=a.pcb_mounts,
        pcb_custom_points=custom_points, pcb_board=a.board,
        oring_compression=a.oring_compression,
        corner_radius=a.corner_radius, seal_corner_radius=a.seal_corner_radius,
        seal_style=a.seal_style,
        connectors=a.connectors, pcb_offset=tuple(a.pcb_offset),
        pcb_extra_cutouts=extra_cutouts,
    )
    if board_screw is not None:
        enc.pcb_screw_hole = board_screw
        enc.m3_clearance = board_clearance
    if a.pcb_hole is not None:  # explicit size beats the preset
        if a.pcb_hole <= 0:
            p.error("--pcb-hole must be positive")
        enc.pcb_screw_hole = a.pcb_hole

    # Warn (but still export) if the chosen standoffs don't fit the cavity.
    if enc.pcb_mounts and custom_points is not None:
        bad = enc.pcb_fit_problems()
        if bad:
            where = ", ".join(f"({x:g}, {y:g})" for x, y in bad)
            print(f"WARNING: {len(bad)} standoff(s) do not fit this box: {where}\n"
                  f"         increase --width/--breadth or use --pcb-offset.")

    path = enc.export_zip(a.outdir)
    if enc.flange:
        note = "flange: round + keyhole" if enc.include_round_holes() else "flange: keyhole only"
    else:
        note = "no flange"
    if enc.pcb_mounts and a.board:
        note += f"; board: {a.board} ({len(enc.pcb_points())} standoffs)"
    else:
        note += f"; PCB standoffs: {len(enc.pcb_points()) if enc.pcb_mounts else 0}"
    nconn = len(enc._resolved_connectors())
    if nconn:
        note += f"; {nconn} connector cutouts"
    print(f"wrote {path}  ({note})")


# ---------------------------------------------------------------------------
# Dispatch: CQ-editor shows objects; plain run / uv run exports.
# ---------------------------------------------------------------------------
try:
    show_object  # type: ignore  # noqa: B018  (injected by CQ-editor)
    _HAVE_SHOW = True
except NameError:
    _HAVE_SHOW = False

    def show_object(*args, **kwargs):  # noqa: D401
        pass

if _HAVE_SHOW:
    _enc = Enclosure()
    _enc.flange = True
    _enc.pcb_mounts = True
    # show_object(_enc.make_base(), name="base")
    # show_object(_enc.make_lid(), name="lid")
    show_object(_enc.make_assembly(), name="assembly")
elif __name__ == "__main__":
    main()
