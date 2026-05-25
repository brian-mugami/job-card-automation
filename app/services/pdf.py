"""Invoice PDF rendering.

The layout matches the existing format used by the garage but reads the
business identity (name, address, phone, email, currency) from
:class:`app.core.config.Settings` so the same codebase can serve multiple
clients via ``.env``.
"""
from __future__ import annotations

from decimal import Decimal
from io import BytesIO

from fastapi import HTTPException, status

from app.core.config import get_settings


def _money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def build_invoice_pdf(context: dict) -> tuple[bytes, str]:
    """Render an invoice to bytes + filename.

    ``context`` is the dict produced by ``invoice_context`` in the API layer.
    Raises 500 if reportlab isn't installed (it's a declared dependency but
    failing soft makes diagnosis easier than an ImportError at import time).
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas
    except ImportError as exc:  # pragma: no cover - declared dependency
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Install reportlab to generate invoice PDFs",
        ) from exc

    settings = get_settings()
    currency = settings.pdf_currency

    invoice = context["invoice"]
    job_card = context["job_card"]
    customer = context["customer"]
    vehicle = context["vehicle"]
    lines = context["lines"]

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    def _draw_header(y_cursor: float) -> float:
        """Header block; called once at the top of each page so multi-page
        invoices keep their column headings visible."""
        pdf.setFont("Helvetica-Bold", 18)
        pdf.drawString(18 * mm, y_cursor, "Invoice")
        pdf.setFont("Helvetica", 9)
        pdf.drawRightString(width - 18 * mm, y_cursor, settings.pdf_company_name)
        y_cursor -= 8 * mm
        pdf.drawRightString(width - 18 * mm, y_cursor, settings.pdf_company_address)
        y_cursor -= 10 * mm

        pdf.drawString(18 * mm, y_cursor, f"Invoice #: {invoice.invoice_number}")
        if invoice.created_at:
            pdf.drawString(75 * mm, y_cursor, f"Date: {invoice.created_at:%d/%m/%Y}")
        y_cursor -= 7 * mm
        pdf.drawString(18 * mm, y_cursor, f"Invoice To: {customer.full_name}")
        y_cursor -= 6 * mm
        pdf.drawString(18 * mm, y_cursor, f"Phone: {customer.phone}")
        y_cursor -= 6 * mm
        pdf.drawString(18 * mm, y_cursor, f"Job Card: {job_card.job_number}")
        pdf.drawString(75 * mm, y_cursor, f"Mileage: {job_card.mileage or ''}")
        pdf.drawString(125 * mm, y_cursor, f"Vehicle: {vehicle.registration}")
        y_cursor -= 12 * mm

        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(18 * mm, y_cursor, "Item")
        pdf.drawString(45 * mm, y_cursor, "Description")
        pdf.drawRightString(130 * mm, y_cursor, "Qty")
        pdf.drawRightString(160 * mm, y_cursor, "Rate")
        pdf.drawRightString(190 * mm, y_cursor, "Amount")
        y_cursor -= 5 * mm
        pdf.line(18 * mm, y_cursor, 190 * mm, y_cursor)
        y_cursor -= 6 * mm
        pdf.setFont("Helvetica", 9)
        return y_cursor

    y = _draw_header(height - 24 * mm)

    for line in lines:
        if y < 35 * mm:
            pdf.showPage()
            y = _draw_header(height - 24 * mm)
        pdf.drawString(18 * mm, y, str(line["item"]))
        # Soft-wrap long descriptions across two rows so nothing gets lost.
        description = str(line["description"])
        if len(description) > 48:
            pdf.drawString(45 * mm, y, description[:48])
            y -= 4 * mm
            pdf.drawString(45 * mm, y, description[48:96])
        else:
            pdf.drawString(45 * mm, y, description)
        pdf.drawRightString(130 * mm, y, f"{_money(line['quantity']):,.2f}")
        pdf.drawRightString(160 * mm, y, f"{_money(line['rate']):,.2f}")
        pdf.drawRightString(190 * mm, y, f"{_money(line['amount']):,.2f}")
        y -= 6 * mm

    y -= 6 * mm
    pdf.line(120 * mm, y, 190 * mm, y)
    y -= 7 * mm
    net_amount = max(
        Decimal("0.00"), _money(invoice.subtotal) - _money(invoice.discount_amount)
    )
    pdf.drawRightString(160 * mm, y, "Line subtotal")
    pdf.drawRightString(190 * mm, y, f"{currency} {invoice.subtotal:,.2f}")
    y -= 6 * mm
    pdf.drawRightString(160 * mm, y, "Discount")
    pdf.drawRightString(190 * mm, y, f"-{currency} {invoice.discount_amount:,.2f}")
    y -= 6 * mm
    pdf.drawRightString(160 * mm, y, "Net amount")
    pdf.drawRightString(190 * mm, y, f"{currency} {net_amount:,.2f}")
    y -= 6 * mm
    pdf.drawRightString(160 * mm, y, f"Tax ({_money(invoice.tax_rate):g}%)")
    pdf.drawRightString(190 * mm, y, f"{currency} {invoice.vat_amount:,.2f}")
    y -= 8 * mm

    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawRightString(160 * mm, y, "Gross total")
    pdf.drawRightString(190 * mm, y, f"{currency} {invoice.total_amount:,.2f}")
    pdf.setFont("Helvetica", 9)
    y -= 14 * mm
    pdf.drawString(18 * mm, y, "Thank you for your business.")
    y -= 6 * mm
    pdf.drawString(
        18 * mm,
        y,
        f"Cell: {settings.pdf_company_phone} | {settings.pdf_company_email}",
    )
    pdf.save()
    buffer.seek(0)
    filename = (
        f"{invoice.invoice_number}_{vehicle.registration}.pdf".replace(" ", "_")
    )
    return buffer.read(), filename
