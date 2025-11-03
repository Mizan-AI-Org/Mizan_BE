# Scheduling backend test guide

This guide helps you validate scheduling features with curl and an importable Postman collection.

Requirements

- Backend running on http://localhost:8000
- Admin user credentials (default from `create_test_user.py`): test@example.com / test123

Postman usage

1. Import environment
   - File: `Mizan_BE/docs/postman/MizanLocal.postman_environment.json`
   - Edit variables if needed (adminEmail/adminPassword, weekStart/weekEnd)
2. Import collection
   - File: `Mizan_BE/docs/postman/Scheduling.postman_collection.json`
3. Run the requests, in order:
   - Auth / Login (Admin)
   - Auth / Me (captures restaurantId)
   - Scheduling / Templates / Create (captures templateId)
   - Scheduling / Template Shifts / Add (adds a WAITER shift on Monday)
   - Scheduling / Weekly Schedules v2 / Create (captures scheduleId)
   - Accounts / Invite Staff (WAITER) → captures inviteToken
   - Accounts / Accept Invitation (Public) → creates a WAITER user and captures waiterId
   - Scheduling / Generate From Template → should now create shifts since WAITER exists
   - Scheduling / Assigned Shifts v2 / List → verify generated shifts
   - Scheduling / Assigned Shifts v2 / Create (Manual) → optional manual creation
   - Scheduling / Weekly Schedules v2 / Publish → mark schedule published

Notes

- If Generate From Template returns "Generated 0 shifts from template", ensure there is at least one active staff in your restaurant whose `role` matches the template shift role. The collection invites and accepts a WAITER to satisfy this.
- ADMIN vs SUPER_ADMIN: endpoints guarded by IsAdmin require `role == ADMIN`. Use the provided admin credentials or update your user role accordingly.
- Date constraints: `week_start` must be a Monday; `weekly schedules` are unique per restaurant + week_start.

Curl quickstart (replace placeholders with actual values)

# Login

curl -s -X POST "http://localhost:8000/api/accounts/auth/login/" \
 -H 'Content-Type: application/json' \
 -d '{"email":"test@example.com","password":"test123"}'

# Me (to get restaurant id)

curl -s -H "Authorization: Bearer $ACCESS" \
 "http://localhost:8000/api/accounts/auth/me/"

# Create template

curl -s -X POST "http://localhost:8000/api/scheduling/templates/" \
 -H 'Content-Type: application/json' \
 -H "Authorization: Bearer $ACCESS" \
 -d '{"name":"Lunch Template","description":"Test","is_active":true}'

# Add template shift (Monday 10-16, WAITER)

curl -s -X POST "http://localhost:8000/api/scheduling/templates/$TEMPLATE_ID/shifts/" \
 -H 'Content-Type: application/json' \
 -H "Authorization: Bearer $ACCESS" \
 -d '{"role":"WAITER","day_of_week":0,"start_time":"10:00:00","end_time":"16:00:00","required_staff":1}'

# Create weekly schedule v2

curl -s -X POST "http://localhost:8000/api/scheduling/weekly-schedules-v2/" \
 -H 'Content-Type: application/json' \
 -H "Authorization: Bearer $ACCESS" \
 -d '{"week_start":"2025-11-03","week_end":"2025-11-09"}'

# Invite a WAITER

curl -s -X POST "http://localhost:8000/api/accounts/staff/invite/" \
 -H 'Content-Type: application/json' \
 -H "Authorization: Bearer $ACCESS" \
 -d '{"email":"waiter1@example.com","role":"WAITER"}'

# Accept invitation (public)

curl -s -X POST "http://localhost:8000/api/accounts/staff/accept-invitation/" \
 -H 'Content-Type: application/json' \
 -d '{"token":"$INVITE_TOKEN","first_name":"Waiter","last_name":"One","pin_code":"1234"}'

# Generate from template

curl -s -X POST "http://localhost:8000/api/scheduling/weekly-schedules-v2/$SCHEDULE_ID/generate_from_template/" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $ACCESS" \
  -d '{"template_id":"'$TEMPLATE_ID'","week_start":"2025-11-03"}'

# List assigned shifts

curl -s -H "Authorization: Bearer $ACCESS" \
  "http://localhost:8000/api/scheduling/assigned-shifts-v2/?schedule_id=$SCHEDULE_ID"

# Manual assigned shift (optional)

curl -s -X POST "http://localhost:8000/api/scheduling/assigned-shifts-v2/" \
 -H 'Content-Type: application/json' \
 -H "Authorization: Bearer $ACCESS" \
  -d '{"schedule":"'$SCHEDULE_ID'","staff":"'$WAITER_ID'","shift_date":"2025-11-03","start_time":"12:00:00","end_time":"18:00:00","break_duration":"00:30:00","role":"WAITER","notes":"Manual"}'

Troubleshooting

- 500 when creating assigned shift via nested endpoint: fixed in this repo (no longer passes an invalid `restaurant` kwarg).
- 403 on templates/schedules: ensure your user has `role == ADMIN`. SUPER_ADMIN isn’t accepted by IsAdmin.
- Overlap errors: shifts for the same staff and date can’t overlap and must be unique per (schedule, staff, date).
