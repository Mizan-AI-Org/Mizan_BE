"""
E2E simulation of WhatsApp incident reporting (photo + caption, plain text, photo then text).
Run: .venv/bin/python manage.py shell -c "exec(open('scripts/test_whatsapp_incident_e2e.py').read())"

Stubs outbound WhatsApp sends and media downloads so nothing hits Meta's API.
Uses Adam's registered phone against the local DB.
"""
import io
import uuid

from django.test import Client
from django.utils import timezone

from accounts.models import CustomUser
from notifications import views as nviews
from notifications.services import notification_service
from notifications.models import Notification, WhatsAppSession
from staff.models_task import SafetyConcernReport

PHONE = "2203736808"  # Adam (digits only, as Meta sends it)
adam = CustomUser.objects.get(id="5f092a20-d090-4050-8bb7-e022aeba482f")

# ---- stubs ---------------------------------------------------------------
sent_messages = []

def fake_send_whatsapp_text(phone, body, *a, **kw):
    sent_messages.append((phone, body))
    print(f"  [WA OUT -> {phone}] {body[:110]!r}")
    return True, {"stubbed": True}

# 1x1 red pixel JPEG
FAKE_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707"
    "07090908080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c23"
    "1c1c2837292c30313434341f27393d38323c2e333432ffc0000b08000100010101"
    "1100ffc40014000100000000000000000000000000000008ffc40014100100000000"
    "00000000000000000000000000ffda0008010100003f00d2cf20ffd9"
)

orig_send = notification_service.send_whatsapp_text
orig_fetch = notification_service.fetch_whatsapp_media_url
orig_download = notification_service.download_media_bytes
orig_lua = notification_service.send_lua_incident

notification_service.send_whatsapp_text = fake_send_whatsapp_text
notification_service.fetch_whatsapp_media_url = lambda mid: (f"https://fake.media/{mid}", "image/jpeg")
notification_service.download_media_bytes = lambda url: FAKE_JPEG
notification_service.send_lua_incident = lambda *a, **kw: print("  [LUA incident notified]")


def make_payload(msg):
    return {
        "entry": [{"changes": [{"value": {
            "messages": [msg],
            "contacts": [{"wa_id": PHONE}],
        }}]}]
    }


def post_webhook(msg):
    c = Client(SERVER_NAME="localhost")
    resp = c.post("/api/notifications/whatsapp/webhook/", data=make_payload(msg), content_type="application/json")
    print(f"  webhook status={resp.status_code}")
    return resp


def reset_session():
    # Pin the session to Adam — dev DB has another user sharing these digits.
    WhatsAppSession.objects.update_or_create(
        phone=PHONE, defaults={"state": "idle", "context": {}, "user": adam}
    )


def latest_ticket():
    return SafetyConcernReport.objects.filter(restaurant=adam.restaurant).order_by("-created_at").first()


def summarize(t):
    print(f"  ticket={str(t.id)[:8]} type={t.incident_type} sev={t.severity} status={t.status}")
    print(f"  title={t.title!r} loc={t.location!r}")
    print(f"  assigned_to={t.assigned_to.first_name if t.assigned_to else None} photo={bool(t.photo)} desc={t.description[:60]!r}")


before_ids = set(SafetyConcernReport.objects.values_list("id", flat=True))

print("\n=== Scenario 1: photo WITH caption 'Broken glass at table 44' (idle session) ===")
reset_session()
post_webhook({
    "from": PHONE, "id": f"wamid.{uuid.uuid4().hex}", "type": "image",
    "image": {"id": "MEDIA123", "mime_type": "image/jpeg", "caption": "Broken glass at table 44"},
})
t1 = latest_ticket()
assert t1 and t1.id not in before_ids, "Scenario 1: no ticket created!"
summarize(t1)
assert t1.incident_type == "Safety", f"expected Safety, got {t1.incident_type}"
assert t1.photo, "photo not attached!"
assert t1.location == "Table 44", f"location={t1.location}"
assert t1.assigned_to is not None, "no assignee routed!"
n1 = Notification.objects.filter(data__incident_id=str(t1.id)).count()
print(f"  in-app manager notifications: {n1}")
assert n1 > 0, "no manager notifications!"

print("\n=== Scenario 2: plain text 'Someone slipped on a wet floor in the kitchen' ===")
reset_session()
before2 = set(SafetyConcernReport.objects.values_list("id", flat=True))
post_webhook({
    "from": PHONE, "id": f"wamid.{uuid.uuid4().hex}", "type": "text",
    "text": {"body": "Someone slipped on a wet floor in the kitchen just now"},
})
t2 = latest_ticket()
assert t2 and t2.id not in before2, "Scenario 2: no ticket created!"
summarize(t2)
assert t2.incident_type == "Safety"
sess2 = WhatsAppSession.objects.get(phone=PHONE)
assert sess2.state == "awaiting_incident_photo", f"expected photo prompt state, got {sess2.state}"
assert any("photo" in m[1].lower() for m in sent_messages[-2:]), "expected photo ask message"

print("\n=== Scenario 2b: 'Broke glass at the bar area' (typo-tolerant) ===")
reset_session()
sent_messages.clear()
before2b = set(SafetyConcernReport.objects.values_list("id", flat=True))
post_webhook({
    "from": PHONE, "id": f"wamid.{uuid.uuid4().hex}", "type": "text",
    "text": {"body": "Broke glass at the bar area"},
})
t2b = latest_ticket()
assert t2b and t2b.id not in before2b, "Scenario 2b: no ticket created!"
summarize(t2b)
assert t2b.incident_type == "Safety"
assert t2b.location == "Bar"

print("\n=== Scenario 3: photo WITHOUT caption, then text description ===")
reset_session()
before3 = set(SafetyConcernReport.objects.values_list("id", flat=True))
post_webhook({
    "from": PHONE, "id": f"wamid.{uuid.uuid4().hex}", "type": "image",
    "image": {"id": "MEDIA456", "mime_type": "image/jpeg"},
})
sess = WhatsAppSession.objects.get(phone=PHONE)
print(f"  session.state={sess.state} (expect awaiting_incident_text)")
assert sess.state == "awaiting_incident_text"
post_webhook({
    "from": PHONE, "id": f"wamid.{uuid.uuid4().hex}", "type": "text",
    "text": {"body": "Broken glass at table 12, happened today"},
})
t3 = latest_ticket()
assert t3 and t3.id not in before3, "Scenario 3: no ticket created!"
summarize(t3)
assert t3.photo, "Scenario 3: photo from earlier message not attached!"
assert t3.location == "Table 12"

# ---- restore + cleanup ----------------------------------------------------
notification_service.send_whatsapp_text = orig_send
notification_service.fetch_whatsapp_media_url = orig_fetch
notification_service.download_media_bytes = orig_download
notification_service.send_lua_incident = orig_lua

print("\nCleanup: deleting test tickets + notifications...")
for t in (t1, t2, t2b, t3):
    Notification.objects.filter(data__incident_id=str(t.id)).delete()
    if t.photo:
        t.photo.delete(save=False)
    t.delete()
reset_session()
print("\nALL SCENARIOS PASSED ✅")
