"""
E2E simulation of WhatsApp staff→manager escalation + clock-in ownership.
Run: .venv/bin/python manage.py shell -c "exec(open('scripts/test_whatsapp_escalation_e2e.py').read())"

Asserts:
1. "Tell my manager that I'm yet to receive my last week wages" creates a
   PAYROLL StaffRequest (Human Resources lane) immediately, notifies managers
   in-app AND on WhatsApp, and never forwards the turn to Lua.
2. A follow-up "Yes, send it" does not duplicate the request.
3. "clock in" is Django-owned (location prompt), and free-text follow-ups
   while awaiting GPS stay in Django (re-prompt) — nothing reaches the LLM.
"""
import uuid

from django.test import Client

from accounts.models import CustomUser
from notifications import views as nviews
from notifications.services import notification_service
from notifications.models import Notification, WhatsAppSession
from staff.models import StaffRequest

PHONE = "2203736808"
adam = CustomUser.objects.get(id="5f092a20-d090-4050-8bb7-e022aeba482f")

sent, lua_forwards = [], []

def fake_send_whatsapp_text(phone, body, *a, **kw):
    sent.append((str(phone), body))
    print(f"  [WA OUT -> {phone}] {body[:100]!r}")
    return True, {"stubbed": True}

def fake_send_location_request(phone, body, *a, **kw):
    sent.append((str(phone), f"[LOCATION REQUEST] {body}"))
    print(f"  [WA OUT -> {phone}] [LOCATION REQUEST] {body[:80]!r}")
    return True, {"stubbed": True}

def fake_forward_to_lua(payload):
    lua_forwards.append(payload)
    print("  [!! FORWARDED TO LUA !!]")

orig = (
    notification_service.send_whatsapp_text,
    getattr(notification_service, "send_whatsapp_location_request_interactive", None),
    nviews._forward_to_lua_whatsapp,
)
notification_service.send_whatsapp_text = fake_send_whatsapp_text
notification_service.send_whatsapp_location_request_interactive = fake_send_location_request
nviews._forward_to_lua_whatsapp = fake_forward_to_lua


def post_text(body, msg_id=None):
    c = Client(SERVER_NAME="localhost")
    payload = {
        "entry": [{"changes": [{"value": {
            "messages": [{
                "from": PHONE, "id": msg_id or f"wamid.{uuid.uuid4().hex}",
                "type": "text", "text": {"body": body},
            }],
            "contacts": [{"wa_id": PHONE}],
        }}]}]
    }
    r = c.post("/api/notifications/whatsapp/webhook/", data=payload, content_type="application/json")
    print(f"  webhook status={r.status_code}")


def reset_session():
    WhatsAppSession.objects.update_or_create(
        phone=PHONE, defaults={"state": "idle", "context": {}, "user": adam}
    )


print("\n=== Scenario 1: wages escalation creates PAYROLL request in HR lane ===")
reset_session()
before = set(StaffRequest.objects.values_list("id", flat=True))
post_text("Tell my manager that I'm yet to receive my last week wages")
new = StaffRequest.objects.exclude(id__in=before)
assert new.count() == 1, f"expected 1 new StaffRequest, got {new.count()}"
req = new.first()
print(f"  request={str(req.id)[:8]} category={req.category} status={req.status} assignee={req.assignee}")
assert req.category == "PAYROLL", f"category={req.category}"
assert req.status == "PENDING"
assert req.restaurant_id == adam.restaurant_id
n = Notification.objects.filter(data__staff_request_id=str(req.id)).count()
print(f"  in-app manager notifications: {n}")
assert n > 0, "no manager in-app notifications!"
staff_replies = [b for p, b in sent if p == PHONE]
assert any("Human Resources" in b for b in staff_replies), "staff reply doesn't mention HR lane"
mgr_pings = [(p, b) for p, b in sent if p != PHONE and "request from" in b]
print(f"  manager WhatsApp pings: {len(mgr_pings)} -> {[p for p, _ in mgr_pings]}")
assert mgr_pings, "no manager WhatsApp ping (owner fallback failed)!"
assert all("Human Resources" in b for _, b in mgr_pings), "manager ping missing lane"
assert not lua_forwards, "escalation turn was forwarded to Lua!"

print("\n=== Scenario 2: 'Yes, send it' after ingest does not duplicate ===")
before2 = set(StaffRequest.objects.values_list("id", flat=True))
post_text("Yes, send it")
dup = StaffRequest.objects.exclude(id__in=before2)
assert dup.count() == 0, f"duplicate request created: {dup.count()}"
assert not lua_forwards, "'Yes, send it' was forwarded to Lua!"
print("  no duplicate, Django replied:", repr(sent[-1][1][:80]))

print("\n=== Scenario 3: clock-in is Django-owned; follow-ups never reach Lua ===")
reset_session()
sent.clear()
lua_forwards.clear()
post_text("clock in")
sess = WhatsAppSession.objects.get(phone=PHONE)
print(f"  session.state={sess.state}")
assert sess.state == "awaiting_clock_in_location", sess.state
assert any("LOCATION REQUEST" in b or "location" in b.lower() for _, b in sent), "no location prompt"
assert not lua_forwards, "'clock in' was forwarded to Lua!"

# Float detour recovery even when session was never set (Space asked for float first).
reset_session()
sent.clear()
post_text("250MAD")
assert not lua_forwards, "'250MAD' leaked to Lua!"
assert WhatsAppSession.objects.get(phone=PHONE).state == "awaiting_clock_in_location"
assert any("LOCATION REQUEST" in b for _, b in sent), "no location re-prompt after float reply"
print("  float follow-up re-prompts location:", repr(sent[-1][1][:80]))

# cleanup
notification_service.send_whatsapp_text = orig[0]
if orig[1]:
    notification_service.send_whatsapp_location_request_interactive = orig[1]
nviews._forward_to_lua_whatsapp = orig[2]
Notification.objects.filter(data__staff_request_id=str(req.id)).delete()
req.delete()
reset_session()
print("\nALL SCENARIOS PASSED ✅")
