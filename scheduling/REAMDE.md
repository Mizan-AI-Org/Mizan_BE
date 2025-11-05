# Scheduling API

This document describes the Scheduling module endpoints, models, permissions, and how to test them.

- Base API URL: http://localhost:8000/api/scheduling/
- Swagger: http://localhost:8000/api/swagger-ui/
- Source
  - URLs: `scheduling/urls.py`
  - Views: `scheduling/views.py`, `scheduling/views_enhanced.py`
  - Models: `scheduling/models.py`
  - Serializers: `scheduling/serializers.py`

## Auth and Roles

- All endpoints require JWT Bearer tokens.
- Roles:
  - Templates and Weekly Schedules: Admins only (`IsAdmin`)
  - Assigned Shifts: Managers/Admins (`IsManagerOrAdmin`)
  - Shift Swap Requests: Authenticated users

Get a token via POST http://localhost:8000/api/auth/login/ with:
```json
{ "email": "admin@example.com", "password": "yourPassword" }
```

Use header:
```
Authorization: Bearer <access_token>
```

## Models (high level)

- ScheduleTemplate: id, restaurant, name, is_active, created_at
- TemplateShift: id, template(FK), role, day_of_week(0=Mon..6=Sun), start_time, end_time, required_staff
- WeeklySchedule: id, restaurant, week_start, week_end, is_published, created_at
- AssignedShift: id, schedule(FK), staff(FK), shift_date, start_time, end_time, break_duration, role, notes, created/updated_at
- ShiftSwapRequest: see serializer (`ShiftSwapRequestSerializer`)

See: `scheduling/models.py`, `scheduling/serializers.py`

## Endpoints

### 1) Schedule Templates
- List/Create: GET/POST /api/scheduling/templates/
  - Body (POST):
  ```json
  { "name": "Weekday Template", "is_active": true }
  ```
- Retrieve/Update/Delete: GET/PATCH/PUT/DELETE /api/scheduling/templates/{template_id}/

Views: `scheduling.views.ScheduleTemplateListCreateAPIView`, `ScheduleTemplateRetrieveUpdateDestroyAPIView`

### 2) Template Shifts (per template)
- List/Create: GET/POST /api/scheduling/templates/{template_id}/shifts/
  - Body (POST):
  ```json
  {
    "role": "WAITER",
    "day_of_week": 0,
    "start_time": "09:00:00",
    "end_time": "17:00:00",
    "required_staff": 2
  }
  ```
- Retrieve/Update/Delete: GET/PATCH/PUT/DELETE /api/scheduling/templates/{template_id}/shifts/{shift_id}/

Views: `scheduling.views.TemplateShiftListCreateAPIView`, `TemplateShiftRetrieveUpdateDestroyAPIView`

### 3) Weekly Schedules (v1 - simple CRUD)
- List/Create: GET/POST /api/scheduling/weekly-schedules/
  - Body (POST):
  ```json
  {
    "week_start": "2025-01-20",
    "week_end": "2025-01-26",
    "is_published": false
  }
  ```
- Retrieve/Update/Delete: GET/PATCH/PUT/DELETE /api/scheduling/weekly-schedules/{schedule_id}/

Views: `scheduling.views.WeeklyScheduleListCreateAPIView`, `WeeklyScheduleRetrieveUpdateDestroyAPIView`

### 4) Assigned Shifts (nested under a weekly schedule)
- List/Create: GET/POST /api/scheduling/weekly-schedules/{schedule_id}/assigned-shifts/
  - Body (POST):
  ```json
  {
    "staff": "USER_UUID",
    "shift_date": "2025-01-21",
    "start_time": "10:00:00",
    "end_time": "18:00:00",
    "break_duration": "00:30:00",
    "role": "CHEF",
    "notes": "Evening prep"
  }
  ```
- Retrieve/Update/Delete: GET/PATCH/PUT/DELETE /api/scheduling/weekly-schedules/{schedule_id}/assigned-shifts/{assigned_shift_id}/

Views: `scheduling.views.AssignedShiftListCreateAPIView`, `AssignedShiftRetrieveUpdateDestroyAPIView`

### 5) Shift Swap Requests
- List/Create: GET/POST /api/scheduling/shift-swap-requests/
  - Typical body (POST, may vary by serializer):
  ```json
  {
    "assigned_shift": "ASSIGNED_SHIFT_UUID",
    "receiver": "OPTIONAL_RECEIVER_USER_UUID",
    "reason": "Need to swap due to conflict"
  }
  ```
- Retrieve/Update/Delete: GET/PATCH/PUT/DELETE /api/scheduling/shift-swap-requests/{swap_request_id}/

Views: `scheduling.views.ShiftSwapRequestListCreateAPIView`, `ShiftSwapRequestRetrieveUpdateDestroyAPIView`

### 6) Enhanced viewsets (v2)
Registered via DRF router in `scheduling/urls.py`.

- Weekly schedules v2: GET /api/scheduling/weekly-schedules-v2/
  - Query params: `date_from=YYYY-MM-DD&date_to=YYYY-MM-DD`
  - Detail: GET /api/scheduling/weekly-schedules-v2/{id}/
  - Analytics: GET /api/scheduling/weekly-schedules-v2/{id}/analytics/
  - Coverage: GET /api/scheduling/weekly-schedules-v2/{id}/coverage/
  ViewSet: `scheduling.views_enhanced.WeeklyScheduleViewSet`

- Assigned shifts v2: GET/POST/… /api/scheduling/assigned-shifts-v2/
  - Confirm shift: POST /api/scheduling/assigned-shifts-v2/{id}/confirm/
  ViewSet: `scheduling.views_enhanced.AssignedShiftViewSet`

- Task categories: /api/scheduling/task-categories/
- Shift tasks: /api/scheduling/shift-tasks/
  - Start task: POST /api/scheduling/shift-tasks/{id}/start/
  - Complete task: POST /api/scheduling/shift-tasks/{id}/complete/
  - Reassign: POST /api/scheduling/shift-tasks/{id}/reassign/
  ViewSets: `scheduling.views_enhanced.TaskCategoryViewSet`, `ShiftTaskViewSet`

- Timesheets: /api/scheduling/timesheets/
- Timesheet entries: /api/scheduling/timesheet-entries/
  ViewSets: `scheduling.views_enhanced.TimesheetViewSet`, `TimesheetEntryViewSet`

## End-to-end test (curl)

1) Login
```bash
curl -X POST http://localhost:8000/api/auth/login/ \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","password":"YourPass"}'
```
Copy `.access` as TOKEN.

2) Create a schedule template
```bash
curl -X POST http://localhost:8000/api/scheduling/templates/ \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"Week A","is_active":true}'
```

3) Add a template shift
```bash
curl -X POST http://localhost:8000/api/scheduling/templates/<TEMPLATE_ID>/shifts/ \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"role":"WAITER","day_of_week":1,"start_time":"09:00:00","end_time":"17:00:00","required_staff":2}'
```

4) Create a weekly schedule
```bash
curl -X POST http://localhost:8000/api/scheduling/weekly-schedules/ \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"week_start":"2025-01-20","week_end":"2025-01-26","is_published":false}'
```

5) Assign a shift to a staff member
```bash
curl -X POST http://localhost:8000/api/scheduling/weekly-schedules/<SCHEDULE_ID>/assigned-shifts/ \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"staff":"<USER_UUID>","shift_date":"2025-01-21","start_time":"10:00:00","end_time":"18:00:00","role":"CHEF","break_duration":"00:30:00","notes":"Evening prep"}'
```

6) List schedules v2 by date range
```bash
curl -X GET "http://localhost:8000/api/scheduling/weekly-schedules-v2/?date_from=2025-01-20&date_to=2025-01-26" \
  -H "Authorization: Bearer $TOKEN"
```

7) Shift analytics/coverage
```bash
curl -X GET http://localhost:8000/api/scheduling/weekly-schedules-v2/<SCHEDULE_ID>/analytics/ \
  -H "Authorization: Bearer $TOKEN"

curl -X GET http://localhost:8000/api/scheduling/weekly-schedules-v2/<SCHEDULE_ID>/coverage/ \
  -H "Authorization: Bearer $TOKEN"
```

8) Confirm an assigned shift (v2)
```bash
curl -X POST http://localhost:8000/api/scheduling/assigned-shifts-v2/<ASSIGNED_SHIFT_ID>/confirm/ \
  -H "Authorization: Bearer $TOKEN"
```

9) Create a swap request
```bash
curl -X POST http://localhost:8000/api/scheduling/shift-swap-requests/ \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"assigned_shift":"<ASSIGNED_SHIFT_ID>","reason":"Need to swap"}'
```

## Troubleshooting

- 401/403: Ensure role permissions (Admin/Manager) and valid Bearer token.
- 404 on “/api/schedule/...”: Use “/api/scheduling/...”.
- Duration field: use "HH:MM:SS" (e.g., "00:30:00") for `break_duration`.
- Staff UUID must belong to your restaurant.

## Frontend pages (optional)

- Schedule Management: http://localhost:8080/dashboard/schedule-management
- Scheduling Dashboard (v2): http://localhost:8080/dashboard/scheduling
