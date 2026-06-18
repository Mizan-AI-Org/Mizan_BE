from decimal import Decimal
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from accounts.models import CustomUser, Restaurant
from finance.attachment_utils import save_invoice_attachment
from finance.models import Invoice


class InvoiceAttachmentUtilsTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Attachment Bistro")
        self.manager = CustomUser.objects.create_user(
            email="m@attach.test",
            password="x",
            first_name="Mona",
            last_name="Manager",
            role="MANAGER",
            restaurant=self.restaurant,
        )
        self.invoice = Invoice.objects.create(
            restaurant=self.restaurant,
            vendor_name="Acme",
            amount=Decimal("120.00"),
            currency="MAD",
            due_date="2026-06-30",
            created_by=self.manager,
        )

    def test_save_pdf_attachment(self):
        pdf_bytes = b"%PDF-1.4 test invoice"
        ok = save_invoice_attachment(
            self.invoice,
            pdf_bytes,
            content_type="application/pdf",
            filename_hint="supplier-invoice.pdf",
        )
        self.assertTrue(ok)
        self.invoice.refresh_from_db()
        self.assertTrue(self.invoice.attachment)
        self.assertEqual(self.invoice.attachment_content_type, "application/pdf")
        self.assertTrue(self.invoice.attachment.name.endswith(".pdf"))

    def test_save_image_also_populates_photo(self):
        png = SimpleUploadedFile("scan.png", b"\x89PNG\r\n", content_type="image/png")
        ok = save_invoice_attachment(
            self.invoice,
            png.read(),
            content_type="image/png",
            filename_hint="scan.png",
        )
        self.assertTrue(ok)
        self.invoice.refresh_from_db()
        self.assertTrue(self.invoice.attachment)
        self.assertTrue(self.invoice.photo)
