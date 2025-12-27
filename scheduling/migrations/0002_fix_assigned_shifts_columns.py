from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("scheduling", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "ALTER TABLE assigned_shifts ADD COLUMN IF NOT EXISTS clock_in_reminder_sent boolean DEFAULT false;\n"
                "ALTER TABLE assigned_shifts ADD COLUMN IF NOT EXISTS check_list_reminder_sent boolean DEFAULT false;"
            ),
            reverse_sql=(
                "ALTER TABLE assigned_shifts DROP COLUMN IF EXISTS clock_in_reminder_sent;\n"
                "ALTER TABLE assigned_shifts DROP COLUMN IF EXISTS check_list_reminder_sent;"
            ),
        )
    ]
