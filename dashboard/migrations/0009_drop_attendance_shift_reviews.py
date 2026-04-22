"""
Drop the legacy `attendance` app tables that backed the now-removed
Shift Reviews feature, and evict its rows from django_migrations so a
fresh `migrate` doesn't trip on a missing app label.

Safe to run multiple times (uses IF EXISTS + conditional delete).
The reverse op is a no-op: once shift reviews are deleted, restoring
the schema from this migration would be meaningless.
"""

from django.db import migrations


SQL_FORWARD = [
    "DROP TABLE IF EXISTS attendance_review_likes CASCADE;",
    "DROP TABLE IF EXISTS attendance_shift_reviews CASCADE;",
    "DELETE FROM django_migrations WHERE app = 'attendance';",
]


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0008_task_source_fields"),
    ]

    operations = [
        migrations.RunSQL(
            sql=";\n".join(SQL_FORWARD),
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
