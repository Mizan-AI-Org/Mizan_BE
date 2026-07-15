from django.test import SimpleTestCase

from accounts.platform_knowledge import search_platform_knowledge


class PlatformKnowledgeSearchTests(SimpleTestCase):
    def test_sales_query(self):
        hits = search_platform_knowledge("today's sales report")
        self.assertTrue(hits)
        self.assertTrue(any("sales" in h["title"].lower() for h in hits))

    def test_audience_filter(self):
        staff = search_platform_knowledge("checklist next", audience="staff")
        self.assertTrue(staff)
        for h in staff:
            self.assertIn(h["audience"], ("staff", "both"))
