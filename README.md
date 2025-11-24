# Mizan Backend

## Directory Structure

mizan-backend/
├── accounts/
    - models.py
    - serializers.py
    - urls.py
    - views.py
├── scheduling/
    - models.py
    - serializers.py
    - urls.py
    - views.py
├── timeclock/
    - models.py
    - serializers.py
    - urls.py
    - views.py
├── reporting/
    - models.py
    - serializers.py
    - urls.py
    - views.py
├── mizan/
    - __init__.py
    - asgi.py
    - settings.py
    - urls.py
    - wsgi.py
└── manage.py
└── reset_db.sh

# Installation

1. Clone the repository
2. Create and activate a virtual environment
3. Install dependencies
4. Run the development server

# Running the Development Server

1. Run the development server with `python manage.py runserver`
2. Open your browser to http://localhost:8000/api/

## Attendance: Shift Reviews & Likes

The `attendance` app enables capturing shift feedback and lightweight peer recognition.

API Endpoints
- `GET /api/attendance/shift-reviews/` — List reviews. Optional filters: `date_from`, `date_to`, `staff_id`, `rating`.
- `POST /api/attendance/shift-reviews/` — Create a review.
  - Body example:
    ```json
    {
      "shift_id": "<uuid>",
      "rating": 5,
      "tags": ["Smooth service flow", "Happy customers"],
      "comments": "Great shift",
      "completed_at_iso": "2025-11-07T12:00:00Z",
      "hours_decimal": 8.0
    }
    ```
- `POST /api/attendance/shift-reviews/<uuid:review_id>/like/` — Toggle like for current user. Response: `{ "liked": true, "likes_count": 3 }`.
- `GET /api/attendance/shift-reviews/stats/` — Aggregates: `by_rating`, `total_reviews`, `total_likes`, `tag_counts`. Optional `date_from`, `date_to`.

Models
- `ShiftReview`: rating, tags, comments, completed_at, hours_decimal, staff, shift, restaurant
- `ReviewLike`: review, user

Configuration
- App registered in `INSTALLED_APPS` as `attendance`.
- Routes included under `/api/attendance/` in `mizan/urls.py`.
- CORS for local dev allows `http://localhost:8080`.
- Apply migrations as needed: `python manage.py migrate`.

## Checklists: Submissions & Manager Review

Endpoints
- `GET /api/checklists/executions/my_checklists/` — List the current staff member’s checklist executions. Supports `status`, `page`, `page_size`, `ordering` (`created_at`, `updated_at`, `completed_at`).
- `GET /api/checklists/executions/submitted/` — For managers/admins, list completed submissions for the current restaurant. Optional `date` (`YYYY-MM-DD`). Paginates by default. Ordered by `-completed_at`.
- `POST /api/checklists/executions/<id>/manager_review/` — Managers/admins approve or reject a submission.
  - Body: `{ "decision": "APPROVED" | "REJECTED", "reason": "optional text" }`
  - Response includes the updated execution payload and `review_status`.

Behavior
- Submissions set `completed_at` using server time in ISO 8601.
- Approval sets `supervisor_approved = true`, `approved_by`, and `approved_at`.
- Rejection sets `supervisor_approved = false` and records an audit entry.

Permissions
- Staff can only access their own executions via `my_checklists`.
- Managers/Admins can access restaurant-wide submissions and perform review actions.
