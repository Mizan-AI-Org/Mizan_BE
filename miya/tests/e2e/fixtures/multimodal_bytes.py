"""Real PDF/image fixture bytes for Phase 14.3.3 PostgreSQL E2E."""
from __future__ import annotations


def _minimal_pdf(*lines: str) -> bytes:
    """Build a minimal valid PDF containing literal text lines."""
    escaped_lines = []
    y = 750
    for line in lines:
        safe = (
            line.replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
        )
        escaped_lines.append(f"BT /F1 10 Tf 50 {y} Td ({safe}) Tj ET")
        y -= 14
    stream_body = "\n".join(escaped_lines)
    stream_len = len(stream_body.encode("utf-8"))
    pdf = f"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj
4 0 obj<</Length {stream_len}>>stream
{stream_body}
endstream
endobj
5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000265 00000 n 
0000000{265 + stream_len + 30:03d} 00000 n 
trailer<</Size 6/Root 1 0 R>>
startxref
{400 + stream_len}
%%EOF"""
    return pdf.encode("utf-8")


def insurance_v1_pdf() -> bytes:
    return _minimal_pdf(
        "MIZAN_FIXTURE:INSURANCE_V1",
        "Restaurant Public Liability Insurance Certificate",
        "Insurer: Atlas Assurance Maroc",
        "Policy Reference: POL-INS-2026-001",
        "Insured Entity: Casablanca Kitchen Site",
        "Issue Date: 2026-01-15",
        "Expiry Date: 2026-09-30",
        "Coverage: Public Liability 2,000,000 MAD",
    )


def insurance_v2_pdf() -> bytes:
    return _minimal_pdf(
        "MIZAN_FIXTURE:INSURANCE_V2",
        "Restaurant Public Liability Insurance Certificate",
        "Insurer: Atlas Assurance Maroc",
        "Policy Reference: POL-INS-2027-001",
        "Insured Entity: Casablanca Kitchen Site",
        "Issue Date: 2027-01-01",
        "Expiry Date: 2027-09-30",
        "Coverage: Public Liability 2,500,000 MAD",
    )


def compliance_certificate_pdf() -> bytes:
    return _minimal_pdf(
        "MIZAN_FIXTURE:COMPLIANCE_V1",
        "Food Hygiene Certificate",
        "Certificate Type: HACCP Level 2",
        "Establishment: Casablanca Kitchen Site",
        "Reference Number: HYG-2026-7788",
        "Issue Date: 2026-02-01",
        "Expiry Date: 2027-02-01",
    )


def invoice_pdf() -> bytes:
    return _minimal_pdf(
        "MIZAN_FIXTURE:INVOICE_V1",
        "TAX INVOICE",
        "Supplier: Fresh Foods Casablanca",
        "Invoice Number: INV-1433-001",
        "Invoice Date: 2026-06-15",
        "Total Amount: 2450.00 MAD",
        "Bill To: Casablanca Kitchen Site",
    )


def establishment_document_pdf() -> bytes:
    return _minimal_pdf(
        "MIZAN_FIXTURE:ESTABLISHMENT_V1",
        "Business Registration Extract",
        "Establishment: Casablanca Kitchen Site",
        "Registration Number: RC-CASA-998877",
        "Issuing Authority: Regional Commerce Office",
    )


def corrupt_pdf() -> bytes:
    return b"%PDF-1.4\nCORRUPT_STREAM_NOT_VALID\n%%EOF"


def empty_pdf() -> bytes:
    return b""


def provider_error_pdf() -> bytes:
    return _minimal_pdf("MIZAN_FIXTURE:PROVIDER_ERROR", "Simulated provider failure")


def image_invoice_jpeg() -> bytes:
    """Minimal JPEG-like bytes with embedded fixture marker."""
    marker = b"MIZAN_FIXTURE:IMAGE_INVOICE_V1"
    # SOI + COM marker segment + minimal tail
    com_len = len(marker) + 2
    return b"\xff\xd8\xff\xfe" + com_len.to_bytes(2, "big") + marker + b"\xff\xd9"
