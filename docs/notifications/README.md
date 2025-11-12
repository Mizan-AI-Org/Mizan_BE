# Announcements & Notifications

This guide covers how admins create announcements and how staff receive, acknowledge, and troubleshoot delivery across channels.

## Admin Guide

- Create announcement: `POST /api/notifications/announcements/create/`
  - Body fields: `title`, `message`, optional targeting (`recipients_staff_ids`, `recipients_departments`, `recipients_roles`, `recipients_shift_ids`), optional `schedule_for` (ISO datetime) and optional `channels` (list of `app`, `email`, `push`, `whatsapp`, `sms`).
- Health check: `GET /api/notifications/health-check/` to validate email, push, WhatsApp, and SMS configurations.
- Review delivery logs in Admin under Notifications → Notification Logs for each channel.

## Staff Guide

- View notifications: `GET /api/notifications/` with filters like `is_read`, `type`, `priority`.
- Acknowledge announcements: `POST /api/notifications/announcements/<notification_id>/ack/` — this records a read receipt.
- Report delivery issues: `POST /api/notifications/announcements/report-issue/` with `description` and optional `notification_id`.
- Mark all read: `POST /api/notifications/mark-all-read/`.

## Troubleshooting

- If you do not receive announcements:
  - Verify Notification Preferences in Profile (email/push/whatsapp/sms enabled).
  - Ensure a device token is registered: `POST /api/notifications/device-tokens/register/`.
  - Report the issue with `report-issue` endpoint.

## Escalation Procedures

- If system health check reports misconfiguration (e.g., email backend or Twilio missing), correct settings in `settings.py` and environment variables.
- For push failures, reinitialize Firebase Admin SDK and clear invalid device tokens.
- For WhatsApp failures, verify `WHATSAPP_ACCESS_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID`.

## Regular Tests

- Run: `python manage.py check_notification_health`.
- Send test notification: `POST /api/notifications/test/` with `channels` to verify each channel.