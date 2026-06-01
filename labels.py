"""
Carton label PDF generator for master cartons.

One A4 page per carton. Each label shows:
  • Ship To (destination)
  • SKU + Code 128 barcode
  • Product name
  • Units per carton
  • CARTON  X  of  Y  (large, at bottom)
"""
from __future__ import annotations

import io

from reportlab.graphics.barcode.code128 import Code128
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas


# ── Public entry point ────────────────────────────────────────────────────────

def generate_carton_labels_pdf(
    shipment: dict,
    lines: list[dict],
    sku_info: dict,
) -> bytes:
    """
    Return a PDF (bytes) — one A4 page per master carton.

    Args:
        shipment : {name, destination, …}
        lines    : [{sku, num_cartons}, …]   (raw DB-style keys)
        sku_info : {sku: {name, carton_units, …}}  from get_sku_catalog_info()
    """
    buf = io.BytesIO()
    W, H = A4  # 595.28 × 841.89 pt

    c = Canvas(buf, pagesize=A4)

    destination = (shipment.get("destination") or "").strip() or "—"
    address     = (shipment.get("address") or "").strip()
    ship_name   = (shipment.get("name") or "").strip()

    page_num = 0
    for line in lines:
        sku          = (line.get("sku") or "").strip()
        num_cartons  = int(line.get("num_cartons") or 0)
        if not sku or num_cartons <= 0:
            continue

        info         = sku_info.get(sku, {})
        product_name = (info.get("name") or sku).strip()
        carton_units = int(info.get("carton_units") or 0)

        for carton_idx in range(1, num_cartons + 1):
            if page_num > 0:
                c.showPage()
            page_num += 1
            _draw_label(
                c, W, H,
                destination   = destination,
                address       = address,
                ship_name     = ship_name,
                sku           = sku,
                product_name  = product_name,
                carton_units  = carton_units,
                carton_num    = carton_idx,
                total_cartons = num_cartons,
            )

    if page_num == 0:
        c.setFont("Helvetica", 14)
        c.setFillColor(colors.grey)
        c.drawCentredString(W / 2, H / 2, "No carton lines found in this shipment.")

    c.save()
    return buf.getvalue()


# ── Label renderer ────────────────────────────────────────────────────────────

_DARK   = colors.HexColor("#2c3e50")
_MUTED  = colors.HexColor("#7f8c8d")
_LIGHT  = colors.HexColor("#ecf0f1")
_WHITE  = colors.white
_BLACK  = colors.black

PAD   = 18 * mm   # outer margin
INNER = 8 * mm    # inner padding from border


def _draw_label(
    c: Canvas,
    W: float,
    H: float,
    *,
    destination: str,
    address: str,
    ship_name: str,
    sku: str,
    product_name: str,
    carton_units: int,
    carton_num: int,
    total_cartons: int,
) -> None:

    x0    = PAD + INNER          # left edge of content
    right = W - PAD - INNER      # right edge of content
    width = right - x0

    # ── Outer border ─────────────────────────────────────────────────
    c.setStrokeColor(_DARK)
    c.setLineWidth(2)
    c.rect(PAD, PAD, W - 2 * PAD, H - 2 * PAD)

    y = H - PAD - 10 * mm

    # ── Shipment name chip (top-right) ────────────────────────────────
    c.setFont("Helvetica", 9)
    c.setFillColor(_MUTED)
    c.drawRightString(right, y, ship_name)

    # ── SHIP TO label ─────────────────────────────────────────────────
    y -= 8 * mm
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(_MUTED)
    c.drawString(x0, y, "SHIP TO")

    # Destination box (destination name + optional address line)
    y -= 5 * mm
    dest_lines = _wrap(destination, max_chars=42)[:2]
    addr_lines = _wrap(address, max_chars=52)[:2] if address else []
    total_lines = len(dest_lines) + len(addr_lines)
    box_h = total_lines * 7 * mm + 6 * mm
    c.setFillColor(_LIGHT)
    c.setStrokeColor(_DARK)
    c.setLineWidth(0.5)
    c.roundRect(x0, y - box_h, width, box_h, 3 * mm, stroke=1, fill=1)

    ty = y - 7 * mm
    # Destination name — bold, larger
    c.setFont("Helvetica-Bold", 15)
    c.setFillColor(_DARK)
    for dl in dest_lines:
        c.drawString(x0 + 5 * mm, ty, dl)
        ty -= 7 * mm
    # Address — regular, slightly smaller
    if addr_lines:
        c.setFont("Helvetica", 12)
        c.setFillColor(colors.HexColor("#34495e"))
        for al in addr_lines:
            c.drawString(x0 + 5 * mm, ty, al)
            ty -= 7 * mm
    y -= box_h + 8 * mm

    # ── Divider ───────────────────────────────────────────────────────
    c.setStrokeColor(colors.HexColor("#bdc3c7"))
    c.setLineWidth(0.5)
    c.line(x0, y, right, y)
    y -= 10 * mm

    # ── SKU heading ───────────────────────────────────────────────────
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(_MUTED)
    c.drawString(x0, y, "SKU")

    y -= 7 * mm
    c.setFont("Helvetica-Bold", 24)
    c.setFillColor(_BLACK)
    c.drawString(x0, y, sku)

    # ── Code 128 barcode ──────────────────────────────────────────────
    y -= 6 * mm
    BAR_H = 20 * mm
    try:
        bar   = Code128(sku, barWidth=0.75 * mm, barHeight=BAR_H, humanReadable=True)
        bar_w = bar.width
        bar_x = x0 + (width - bar_w) / 2
        bar.drawOn(c, bar_x, y - BAR_H - 5 * mm)   # 5 mm extra for human-readable text
    except Exception:
        # Fallback: just print the SKU as text if barcode fails
        c.setFont("Courier", 11)
        c.setFillColor(_DARK)
        c.drawCentredString(W / 2, y - 8 * mm, f"[{sku}]")

    y -= BAR_H + 12 * mm

    # ── Divider ───────────────────────────────────────────────────────
    c.setStrokeColor(colors.HexColor("#bdc3c7"))
    c.setLineWidth(0.5)
    c.line(x0, y, right, y)
    y -= 10 * mm

    # ── Product name ──────────────────────────────────────────────────
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(_MUTED)
    c.drawString(x0, y, "PRODUCT")

    y -= 7 * mm
    c.setFont("Helvetica", 14)
    c.setFillColor(_BLACK)
    for pl in _wrap(product_name, max_chars=50)[:2]:
        c.drawString(x0, y, pl)
        y -= 6 * mm

    # ── Units per carton ──────────────────────────────────────────────
    y -= 4 * mm
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(_MUTED)
    c.drawString(x0, y, "UNITS PER CARTON")

    y -= 7 * mm
    c.setFont("Helvetica-Bold", 20)
    c.setFillColor(_BLACK)
    c.drawString(x0, y, str(carton_units) if carton_units else "—")

    # ── CARTON X of Y — big dark box at bottom ────────────────────────
    BOX_H      = 38 * mm
    box_bottom = PAD + 8 * mm
    box_top    = box_bottom + BOX_H

    c.setFillColor(_DARK)
    c.setStrokeColor(_DARK)
    c.roundRect(x0, box_bottom, width, BOX_H, 4 * mm, stroke=0, fill=1)

    c.setFillColor(_WHITE)
    c.setFont("Helvetica", 13)
    c.drawCentredString(W / 2, box_bottom + BOX_H - 10 * mm, "CARTON")

    c.setFont("Helvetica-Bold", 36)
    c.drawCentredString(W / 2, box_bottom + 8 * mm,
                        f"{carton_num}  of  {total_cartons}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _wrap(text: str, max_chars: int) -> list[str]:
    """Word-wrap text into lines of ≤ max_chars."""
    words   = text.split()
    lines: list[str] = []
    current = ""
    for w in words:
        candidate = (current + " " + w).strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines or [text[:max_chars]]
