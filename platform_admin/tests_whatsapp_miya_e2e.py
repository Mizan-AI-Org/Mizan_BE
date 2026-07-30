"""
E2E: Platform Admin Meta WhatsApp connect → central number activation →
Staff + Manager chat with Miya agent over WhatsApp webhook.

Run:
  .venv/bin/python manage.py test platform_admin.tests_whatsapp_miya_e2e -v2
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import CustomUser, Restaurant, StaffActivationRecord
from notifications.models import WhatsAppSession
from platform_admin.models import PlatformWhatsAppConfig
from platform_admin.whatsapp_services import effective_whatsapp_values, get_or_create_singleton_config


CENTRAL_PHONE = "212784476751"
STAFF_PHONE = "212600111222"
MANAGER_PHONE = "212600333444"
VERIFY_TOKEN = "mizan-e2e-verify-token"
PHONE_NUMBER_ID = "100234567890123"
WABA_ID = "100234567890456"
ACCESS_TOKEN = "EAAe2eTestTokenPermanent1234567890"


def _wa_payload(from_phone: str, body: str, msg_id: str | None = None) -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": from_phone,
                                    "id": msg_id or f"wamid.{uuid.uuid4().hex}",
                                    "type": "text",
                                    "text": {"body": body},
                                }
                            ],
                            "contacts": [{"wa_id": from_phone}],
                        }
                    }
                ]
            }
        ]
    }


@override_settings(
    WHATSAPP_ACCESS_TOKEN="",
    WHATSAPP_PHONE_NUMBER_ID="",
    WHATSAPP_BUSINESS_ACCOUNT_ID="",
    WHATSAPP_VERIFY_TOKEN="",
    WHATSAPP_ACTIVATION_WA_PHONE="",
    MIYA_WHATSAPP_ENABLED=True,
    LUA_WHATSAPP_WEBHOOK_URL="",
    PLATFORM_OPS_EMAILS=["ops-e2e@heymizan.ai"],
    ALLOWED_HOSTS=["localhost", "127.0.0.1", "testserver"],
)
class WhatsAppMiyaConnectE2ETests(TestCase):
    """Full path: ops saves Meta creds → test connection → webhook verify → staff/manager ↔ Miya."""

    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="E2E Miya Cafe")
        self.ops = CustomUser.objects.create_user(
            email="ops-e2e@heymizan.ai",
            password="OpsPass123!",
            first_name="Mizan",
            last_name="Ops",
            role="SUPER_ADMIN",
            is_staff=True,
            is_platform_operator=True,
        )
        self.api = APIClient()
        self.api.defaults["SERVER_NAME"] = "localhost"
        self.api.force_authenticate(user=self.ops)
        self.client = Client(SERVER_NAME="localhost")
        self.wa_out: list[tuple[str, str]] = []

        # Fresh singleton (migration may leave empty row)
        PlatformWhatsAppConfig.objects.filter(pk=PlatformWhatsAppConfig.SINGLETON_ID).delete()

    def _capture_wa_text(self, phone, body, *a, **kw):
        self.wa_out.append((str(phone), str(body)))
        return True, {"stubbed": True}

    def _capture_welcome(self, phone, first_name="", restaurant_name="", *a, **kw):
        self.wa_out.append((str(phone), f"[WELCOME] Hi {first_name} — {restaurant_name}"))
        return True, {"stubbed": True}

    # ------------------------------------------------------------------
    # 1) Platform Admin Meta connect + live probe + webhook verify
    # ------------------------------------------------------------------

    @patch("core.whatsapp_config.requests.get")
    def test_01_ops_connects_central_meta_account(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": PHONE_NUMBER_ID,
            "display_phone_number": "+212 7 84 47 67 51",
            "verified_name": "Mizan AI",
        }
        mock_get.return_value = mock_resp

        # Save config via Platform Admin API
        put = self.api.put(
            "/api/platform/whatsapp/config/",
            {
                "phone_number_id": PHONE_NUMBER_ID,
                "business_account_id": WABA_ID,
                "access_token": ACCESS_TOKEN,
                "verify_token": VERIFY_TOKEN,
                "activation_phone": CENTRAL_PHONE,
                "api_version": "v22.0",
                "miya_whatsapp_enabled": True,
                "miya_voice_default": False,
            },
            format="json",
        )
        self.assertEqual(put.status_code, 200, put.content)
        body = put.json()
        self.assertTrue(body["access_token_set"])
        self.assertEqual(body["phone_number_id"], PHONE_NUMBER_ID)
        self.assertEqual(body["activation_phone"], CENTRAL_PHONE)
        self.assertTrue(body["miya_whatsapp_enabled"])
        self.assertIn("/api/notifications/whatsapp/webhook/", body["webhook_callback_url"])

        # Effective runtime values come from DB (not empty env)
        effective = effective_whatsapp_values()
        self.assertEqual(effective["phone_number_id"], PHONE_NUMBER_ID)
        self.assertEqual(effective["access_token"], ACCESS_TOKEN)
        self.assertEqual(effective["verify_token"], VERIFY_TOKEN)
        self.assertEqual(effective["activation_phone"], CENTRAL_PHONE)

        # Test API Connection (Meta probe)
        test = self.api.post("/api/platform/whatsapp/config/test/", {}, format="json")
        self.assertEqual(test.status_code, 200, test.content)
        test_body = test.json()
        self.assertTrue(test_body["ok"])
        self.assertEqual(test_body.get("verified_name"), "Mizan AI")
        self.assertTrue(test_body["config"]["connected"])
        self.assertTrue(test_body["config"]["last_probe_ok"])

        mock_get.assert_called()
        called_url = mock_get.call_args[0][0]
        self.assertIn(PHONE_NUMBER_ID, called_url)

        # Meta webhook verification handshake (activates the number in Meta)
        verify = self.client.get(
            "/api/notifications/whatsapp/webhook/",
            {
                "hub.mode": "subscribe",
                "hub.verify_token": VERIFY_TOKEN,
                "hub.challenge": "e2e-challenge-42",
            },
        )
        self.assertEqual(verify.status_code, 200)
        self.assertEqual(verify.json(), 42)  # DRF Response(int) → JSON number

        # Wrong verify token must fail
        bad = self.client.get(
            "/api/notifications/whatsapp/webhook/",
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong-token",
                "hub.challenge": "nope",
            },
        )
        self.assertEqual(bad.status_code, 403)

    # ------------------------------------------------------------------
    # 2) Staff ONE-TAP activate + Miya conversation
    # ------------------------------------------------------------------

    @patch("miya.services.whatsapp.run_miya_chat")
    def test_02_staff_activates_and_chats_with_miya(self, mock_miya):
        self._seed_connected_config()
        mock_miya.return_value = {
            "reply": "Hi Staffer — I’m Miya. Your next shift is tomorrow at 10:00.",
            "tool_calls": [],
        }

        StaffActivationRecord.objects.create(
            restaurant=self.restaurant,
            phone=STAFF_PHONE,
            first_name="Staffer",
            last_name="One",
            role="WAITER",
            status=StaffActivationRecord.STATUS_NOT_ACTIVATED,
        )

        with patch(
            "notifications.services.notification_service.send_whatsapp_text",
            side_effect=self._capture_wa_text,
        ), patch(
            "notifications.services.notification_service.send_staff_activated_welcome",
            side_effect=self._capture_welcome,
        ):
            # First inbound → ONE-TAP activation (welcome only; no Miya yet)
            r1 = self.client.post(
                "/api/notifications/whatsapp/webhook/",
                data=_wa_payload(STAFF_PHONE, "Hi Miya"),
                content_type="application/json",
            )
            self.assertEqual(r1.status_code, 200)
            mock_miya.assert_not_called()

            record = StaffActivationRecord.objects.get(phone=STAFF_PHONE)
            self.assertEqual(record.status, StaffActivationRecord.STATUS_ACTIVATED)
            self.assertIsNotNone(record.user_id)
            staff = record.user
            self.assertEqual(staff.role, "WAITER")
            self.assertTrue(any("[WELCOME]" in b for _, b in self.wa_out), self.wa_out)

            session = WhatsAppSession.objects.get(phone=STAFF_PHONE)
            self.assertEqual(session.user_id, staff.id)

            # Second inbound → Miya agent reply
            self.wa_out.clear()
            r2 = self.client.post(
                "/api/notifications/whatsapp/webhook/",
                data=_wa_payload(STAFF_PHONE, "When is my next shift?"),
                content_type="application/json",
            )
            self.assertEqual(r2.status_code, 200)
            mock_miya.assert_called_once()
            call_kw = mock_miya.call_args.kwargs
            self.assertEqual(call_kw["user"].id, staff.id)
            self.assertEqual(call_kw["user_message"], "When is my next shift?")

            staff_replies = [b for p, b in self.wa_out if p == STAFF_PHONE]
            self.assertTrue(
                any("Miya" in b and "shift" in b.lower() for b in staff_replies),
                staff_replies,
            )

            session.refresh_from_db()
            history = (session.context or {}).get("miya_chat_history") or []
            self.assertEqual(len(history), 2)
            self.assertEqual(history[0]["role"], "user")
            self.assertEqual(history[1]["role"], "assistant")

    # ------------------------------------------------------------------
    # 3) Manager communicates with Miya (CRUD-capable role)
    # ------------------------------------------------------------------

    @patch("miya.services.whatsapp.run_miya_chat")
    def test_03_manager_chats_with_miya(self, mock_miya):
        self._seed_connected_config()
        mock_miya.return_value = {
            "reply": "Done — I created a closing checklist task for tonight.",
            "tool_calls": [{"name": "create_dashboard_task"}],
        }

        manager = CustomUser.objects.create_user(
            email="mgr-e2e@miya.test",
            password="MgrPass123!",
            first_name="Mana",
            last_name="Ger",
            role="MANAGER",
            restaurant=self.restaurant,
            phone=MANAGER_PHONE,
            is_active=True,
            is_verified=True,
        )
        WhatsAppSession.objects.create(phone=MANAGER_PHONE, user=manager, state="idle", context={})

        with patch(
            "notifications.services.notification_service.send_whatsapp_text",
            side_effect=self._capture_wa_text,
        ):
            r = self.client.post(
                "/api/notifications/whatsapp/webhook/",
                data=_wa_payload(MANAGER_PHONE, "Create a closing checklist task for tonight"),
                content_type="application/json",
            )
            self.assertEqual(r.status_code, 200)
            mock_miya.assert_called_once()
            self.assertEqual(mock_miya.call_args.kwargs["user"].id, manager.id)

            replies = [b for p, b in self.wa_out if p == MANAGER_PHONE]
            self.assertTrue(any("checklist" in b.lower() for b in replies), replies)

    # ------------------------------------------------------------------
    # 4) End-to-end single scenario: connect → activate staff → both talk
    # ------------------------------------------------------------------

    @patch("miya.services.whatsapp.run_miya_chat")
    @patch("core.whatsapp_config.requests.get")
    def test_04_full_connect_activate_staff_and_manager(self, mock_get, mock_miya):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": PHONE_NUMBER_ID,
            "display_phone_number": "+212 784476751",
            "verified_name": "Mizan Central",
        }
        mock_get.return_value = mock_resp

        replies = {
            STAFF_PHONE: {"reply": "Staff ack from Miya.", "tool_calls": []},
            MANAGER_PHONE: {"reply": "Manager ack from Miya — shift created.", "tool_calls": []},
        }

        def miya_side_effect(*, user, user_message, **kw):
            phone = "".join(filter(str.isdigit, user.phone or ""))
            return replies.get(phone, {"reply": "Hello from Miya.", "tool_calls": []})

        mock_miya.side_effect = miya_side_effect

        # A) Connect central Meta account
        save = self.api.put(
            "/api/platform/whatsapp/config/",
            {
                "phone_number_id": PHONE_NUMBER_ID,
                "business_account_id": WABA_ID,
                "access_token": ACCESS_TOKEN,
                "verify_token": VERIFY_TOKEN,
                "activation_phone": CENTRAL_PHONE,
                "miya_whatsapp_enabled": True,
            },
            format="json",
        )
        self.assertEqual(save.status_code, 200)
        test = self.api.post("/api/platform/whatsapp/config/test/", {}, format="json")
        self.assertEqual(test.status_code, 200)
        self.assertTrue(test.json()["ok"])

        verify = self.client.get(
            "/api/notifications/whatsapp/webhook/",
            {
                "hub.mode": "subscribe",
                "hub.verify_token": VERIFY_TOKEN,
                "hub.challenge": "99",
            },
        )
        self.assertEqual(verify.status_code, 200)
        self.assertEqual(verify.json(), 99)

        # B) Pending staff + already-active manager
        StaffActivationRecord.objects.create(
            restaurant=self.restaurant,
            phone=STAFF_PHONE,
            first_name="Ali",
            last_name="Staff",
            role="WAITER",
            status=StaffActivationRecord.STATUS_NOT_ACTIVATED,
        )
        manager = CustomUser.objects.create_user(
            email="mgr-full@miya.test",
            password="MgrPass123!",
            first_name="Sara",
            last_name="Manager",
            role="MANAGER",
            restaurant=self.restaurant,
            phone=MANAGER_PHONE,
            is_active=True,
            is_verified=True,
        )
        WhatsAppSession.objects.create(phone=MANAGER_PHONE, user=manager, state="idle", context={})

        with patch(
            "notifications.services.notification_service.send_whatsapp_text",
            side_effect=self._capture_wa_text,
        ), patch(
            "notifications.services.notification_service.send_staff_activated_welcome",
            side_effect=self._capture_welcome,
        ):
            # Staff activates on first message
            self.client.post(
                "/api/notifications/whatsapp/webhook/",
                data=_wa_payload(STAFF_PHONE, "hello"),
                content_type="application/json",
            )
            self.assertEqual(
                StaffActivationRecord.objects.get(phone=STAFF_PHONE).status,
                StaffActivationRecord.STATUS_ACTIVATED,
            )

            # Staff talks to Miya
            self.client.post(
                "/api/notifications/whatsapp/webhook/",
                data=_wa_payload(STAFF_PHONE, "What are my shifts?"),
                content_type="application/json",
            )

            # Manager talks to Miya
            self.client.post(
                "/api/notifications/whatsapp/webhook/",
                data=_wa_payload(MANAGER_PHONE, "Create a lunch shift for Ali"),
                content_type="application/json",
            )

        staff_msgs = [b for p, b in self.wa_out if p == STAFF_PHONE]
        mgr_msgs = [b for p, b in self.wa_out if p == MANAGER_PHONE]
        self.assertTrue(any("Staff ack" in b for b in staff_msgs), staff_msgs)
        self.assertTrue(any("Manager ack" in b for b in mgr_msgs), mgr_msgs)
        self.assertEqual(mock_miya.call_count, 2)

        # Config still connected after traffic
        cfg = self.api.get("/api/platform/whatsapp/config/")
        self.assertEqual(cfg.status_code, 200)
        self.assertTrue(cfg.json()["connected"])
        self.assertTrue(cfg.json()["miya_whatsapp_enabled"])

    def _seed_connected_config(self):
        row = get_or_create_singleton_config()
        from platform_admin.whatsapp_services import save_config

        save_config(
            {
                "phone_number_id": PHONE_NUMBER_ID,
                "business_account_id": WABA_ID,
                "access_token": ACCESS_TOKEN,
                "verify_token": VERIFY_TOKEN,
                "activation_phone": CENTRAL_PHONE,
                "miya_whatsapp_enabled": True,
            },
            self.ops,
        )
        row.refresh_from_db()
        row.last_probe_ok = True
        row.display_phone_number = "+212 784476751"
        row.verified_name = "Mizan AI"
        row.save(update_fields=["last_probe_ok", "display_phone_number", "verified_name"])
