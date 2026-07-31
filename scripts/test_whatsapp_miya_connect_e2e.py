"""
E2E: Platform Admin Meta connect → webhook verify → Staff + Manager ↔ Miya.

Uses the local DB (same pattern as scripts/test_whatsapp_escalation_e2e.py).
Meta Graph + Miya LLM are mocked; WhatsApp outbound is stubbed.

Run:
  .venv/bin/python manage.py shell < scripts/test_whatsapp_miya_connect_e2e.py
  # or:
  .venv/bin/python manage.py shell -c "exec(open('scripts/test_whatsapp_miya_connect_e2e.py').read())"
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from django.test import Client, override_settings
from rest_framework.test import APIClient

from accounts.models import CustomUser, Restaurant, StaffActivationRecord
from notifications.models import WhatsAppSession, WhatsAppMessageProcessed
from platform_admin.models import PlatformWhatsAppConfig
from platform_admin.whatsapp_services import effective_whatsapp_values, save_config

CENTRAL_PHONE = "212784476751"
STAFF_PHONE = "212699001122"
MANAGER_PHONE = "212699003344"
VERIFY_TOKEN = "mizan-e2e-verify-token"
PHONE_NUMBER_ID = "100234567890123"
WABA_ID = "100234567890456"
ACCESS_TOKEN = "EAAe2eTestTokenPermanent1234567890"


def _payload(from_phone: str, body: str) -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": from_phone,
                                    "id": f"wamid.e2e.{uuid.uuid4().hex}",
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


print("\n=== WhatsApp ↔ Miya connect E2E ===\n")

# Isolate from env secrets for this run
settings_ctx = override_settings(
    WHATSAPP_ACCESS_TOKEN="",
    WHATSAPP_PHONE_NUMBER_ID="",
    WHATSAPP_BUSINESS_ACCOUNT_ID="",
    WHATSAPP_VERIFY_TOKEN="",
    WHATSAPP_ACTIVATION_WA_PHONE="",
    MIYA_WHATSAPP_ENABLED=True,
    ALLOWED_HOSTS=["localhost", "127.0.0.1", "testserver", "app.heymizan.ai", "api.heymizan.ai"],
)
settings_ctx.enable()

# Snapshot existing platform WhatsApp config so we can restore after the run
_prior_cfg = PlatformWhatsAppConfig.objects.filter(
    pk=PlatformWhatsAppConfig.SINGLETON_ID
).first()
_prior_snapshot = None
if _prior_cfg:
    _prior_snapshot = {
        f.name: getattr(_prior_cfg, f.name)
        for f in PlatformWhatsAppConfig._meta.fields
        if f.name != "id"
    }

_rid = uuid.uuid4().hex[:8]
restaurant = Restaurant.objects.create(
    name=f"E2E Miya Cafe {_rid}",
    email=f"e2e-miya-{_rid}@test.mizan.local",
)
ops, _ = CustomUser.objects.get_or_create(
    email="ops-e2e@heymizan.ai",
    defaults={
        "first_name": "Mizan",
        "last_name": "Ops",
        "role": "SUPER_ADMIN",
        "is_staff": True,
        "is_platform_operator": True,
        "is_active": True,
    },
)
if not ops.is_platform_operator:
    ops.is_platform_operator = True
    ops.is_staff = True
    ops.save(update_fields=["is_platform_operator", "is_staff"])

api = APIClient()
api.defaults["SERVER_NAME"] = "localhost"
api.force_authenticate(user=ops)
client = Client(SERVER_NAME="localhost")
wa_out: list[tuple[str, str]] = []

# Reset singleton config for a clean connect path
PlatformWhatsAppConfig.objects.filter(pk=PlatformWhatsAppConfig.SINGLETON_ID).delete()
WhatsAppSession.objects.filter(phone__in=[STAFF_PHONE, MANAGER_PHONE]).delete()
WhatsAppMessageProcessed.objects.filter(wamid__startswith="wamid.e2e.").delete()
StaffActivationRecord.objects.filter(phone__in=[STAFF_PHONE, MANAGER_PHONE]).delete()
CustomUser.objects.filter(email__in=[
    f"wa_{STAFF_PHONE}@mizan.activation",
    "mgr-e2e-full@miya.test",
]).delete()

passed = 0


def ok(label: str):
    global passed
    passed += 1
    print(f"  ✅ {label}")


try:
    # ------------------------------------------------------------------
    # 1) Ops saves Meta credentials + Test Connection + webhook verify
    # ------------------------------------------------------------------
    print("1) Connect central Meta account (Platform Admin)")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "id": PHONE_NUMBER_ID,
        "display_phone_number": "+212 7 84 47 67 51",
        "verified_name": "Mizan AI",
    }

    with patch("core.whatsapp_config.requests.get", return_value=mock_resp):
        put = api.put(
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
        assert put.status_code == 200, put.content
        body = put.json()
        assert body["access_token_set"], body
        assert body["phone_number_id"] == PHONE_NUMBER_ID
        assert body["activation_phone"] == CENTRAL_PHONE
        assert "/api/notifications/whatsapp/webhook/" in body["webhook_callback_url"]
        ok("PUT /api/platform/whatsapp/config/ saved credentials")

        effective = effective_whatsapp_values()
        assert effective["access_token"] == ACCESS_TOKEN
        assert effective["phone_number_id"] == PHONE_NUMBER_ID
        assert effective["verify_token"] == VERIFY_TOKEN
        ok("Runtime effective_whatsapp_values() reads DB config")

        test = api.post("/api/platform/whatsapp/config/test/", {}, format="json")
        assert test.status_code == 200, test.content
        assert test.json()["ok"] is True
        assert test.json()["config"]["connected"] is True
        ok("POST /whatsapp/config/test/ Meta probe OK (Connected)")

    verify = client.get(
        "/api/notifications/whatsapp/webhook/",
        {
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "4242",
        },
    )
    assert verify.status_code == 200, verify.content
    assert verify.json() == 4242
    ok("Webhook verify token handshake (number activation)")

    bad = client.get(
        "/api/notifications/whatsapp/webhook/",
        {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"},
    )
    assert bad.status_code == 403
    ok("Wrong verify token rejected")

    # ------------------------------------------------------------------
    # 2) Staff ONE-TAP activate + Miya chat
    # ------------------------------------------------------------------
    print("\n2) Staff activates on central number and chats with Miya")
    StaffActivationRecord.objects.create(
        restaurant=restaurant,
        phone=STAFF_PHONE,
        first_name="Ali",
        last_name="Staff",
        role="WAITER",
        status=StaffActivationRecord.STATUS_NOT_ACTIVATED,
    )

    def capture_text(phone, body, *a, **kw):
        wa_out.append((str(phone), str(body)))
        print(f"  [WA OUT -> {phone}] {str(body)[:120]!r}")
        return True, {"stubbed": True}

    def capture_welcome(phone, first_name="", restaurant_name="", *a, **kw):
        msg = f"[WELCOME] Hi {first_name} — {restaurant_name}"
        wa_out.append((str(phone), msg))
        print(f"  [WA OUT -> {phone}] {msg!r}")
        return True, {"stubbed": True}

    miya_calls = []

    def fake_miya(*, user, user_message, **kw):
        miya_calls.append({"user_id": str(user.id), "role": user.role, "message": user_message})
        if user.role == "MANAGER":
            return {"reply": "Manager ack from Miya — shift created.", "tool_calls": []}
        return {"reply": "Staff ack from Miya — your next shift is tomorrow.", "tool_calls": []}

    with patch(
        "notifications.services.notification_service.send_whatsapp_text",
        side_effect=capture_text,
    ), patch(
        "notifications.services.notification_service.send_staff_activated_welcome",
        side_effect=capture_welcome,
    ), patch(
        "miya.services.whatsapp.run_miya_chat",
        side_effect=fake_miya,
    ):
        r1 = client.post(
            "/api/notifications/whatsapp/webhook/",
            data=_payload(STAFF_PHONE, "Hi Miya"),
            content_type="application/json",
        )
        assert r1.status_code == 200, r1.content
        record = StaffActivationRecord.objects.get(phone=STAFF_PHONE)
        assert record.status == StaffActivationRecord.STATUS_ACTIVATED, record.status
        assert record.user_id
        assert any("[WELCOME]" in b for _, b in wa_out), wa_out
        assert not miya_calls, "activation turn should not call Miya"
        ok("Staff ONE-TAP activated on first inbound message")

        wa_out.clear()
        r2 = client.post(
            "/api/notifications/whatsapp/webhook/",
            data=_payload(STAFF_PHONE, "When is my next shift?"),
            content_type="application/json",
        )
        assert r2.status_code == 200, r2.content
        assert len(miya_calls) == 1, miya_calls
        assert miya_calls[0]["role"] == "WAITER"
        assert miya_calls[0]["message"] == "When is my next shift?"
        assert any("Staff ack from Miya" in b for p, b in wa_out if p == STAFF_PHONE), wa_out
        sess = WhatsAppSession.objects.get(phone=STAFF_PHONE)
        hist = (sess.context or {}).get("miya_chat_history") or []
        assert len(hist) == 2, hist
        ok("Staff conversational turn reached Miya and got WhatsApp reply")

        # ------------------------------------------------------------------
        # 3) Manager chats with Miya
        # ------------------------------------------------------------------
        print("\n3) Manager communicates with Miya")
        manager = CustomUser.objects.create_user(
            email="mgr-e2e-full@miya.test",
            password="MgrPass123!",
            first_name="Sara",
            last_name="Manager",
            role="MANAGER",
            restaurant=restaurant,
            phone=MANAGER_PHONE,
            is_active=True,
            is_verified=True,
        )
        WhatsAppSession.objects.create(
            phone=MANAGER_PHONE, user=manager, state="idle", context={}
        )
        wa_out.clear()
        before = len(miya_calls)
        r3 = client.post(
            "/api/notifications/whatsapp/webhook/",
            data=_payload(MANAGER_PHONE, "Create a lunch shift for Ali"),
            content_type="application/json",
        )
        assert r3.status_code == 200, r3.content
        assert len(miya_calls) == before + 1, miya_calls
        assert miya_calls[-1]["role"] == "MANAGER"
        assert any("Manager ack from Miya" in b for p, b in wa_out if p == MANAGER_PHONE), wa_out
        ok("Manager conversational turn reached Miya and got WhatsApp reply")

    cfg = api.get("/api/platform/whatsapp/config/")
    assert cfg.status_code == 200
    assert cfg.json()["connected"] is True
    assert cfg.json()["miya_whatsapp_enabled"] is True
    ok("Config still Connected after staff + manager traffic")

    print(f"\nALL {passed} CHECKS PASSED ✅")
    print("Central number ready for Meta: webhook verified, Miya enabled for Staff + Manager.\n")

finally:
    # Cleanup ephemeral fixtures
    WhatsAppSession.objects.filter(phone__in=[STAFF_PHONE, MANAGER_PHONE]).delete()
    WhatsAppMessageProcessed.objects.filter(wamid__startswith="wamid.e2e.").delete()
    StaffActivationRecord.objects.filter(restaurant=restaurant).delete()
    CustomUser.objects.filter(
        email__in=[
            f"wa_{STAFF_PHONE}@mizan.activation",
            "mgr-e2e-full@miya.test",
        ]
    ).delete()
    CustomUser.objects.filter(phone__in=[STAFF_PHONE, MANAGER_PHONE], restaurant=restaurant).delete()
    restaurant.delete()

    # Restore prior WhatsApp config (or clear E2E stubs)
    PlatformWhatsAppConfig.objects.filter(pk=PlatformWhatsAppConfig.SINGLETON_ID).delete()
    if _prior_snapshot is not None:
        PlatformWhatsAppConfig.objects.create(
            id=PlatformWhatsAppConfig.SINGLETON_ID, **_prior_snapshot
        )
        print("Restored previous Platform WhatsApp config.")
    settings_ctx.disable()
