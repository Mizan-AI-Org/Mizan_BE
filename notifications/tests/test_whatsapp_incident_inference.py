"""Tests for WhatsApp incident inference helpers."""

from django.test import SimpleTestCase

from notifications.utils import (
    extract_incident_location,
    infer_incident_type,
    infer_severity,
    looks_like_whatsapp_incident_report,
)


class WhatsAppIncidentInferenceTests(SimpleTestCase):
    def test_broken_glass_is_safety_not_maintenance(self):
        msg = "Broken glass at table 44"
        self.assertEqual(infer_incident_type(msg), "Safety")
        self.assertEqual(infer_severity(msg), "HIGH")
        self.assertTrue(looks_like_whatsapp_incident_report(msg))
        self.assertEqual(extract_incident_location(msg), "Table 44")

    def test_plain_broken_fridge_is_maintenance(self):
        self.assertEqual(infer_incident_type("The fridge is broken"), "Maintenance")

    def test_slip_is_safety(self):
        self.assertEqual(infer_incident_type("Customer slipped near the bar"), "Safety")
