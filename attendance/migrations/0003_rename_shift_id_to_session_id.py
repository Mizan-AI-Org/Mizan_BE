from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("attendance", "0002_remove_reviewlike_unique_review_user_like_and_more"),
    ]

    operations = [
        migrations.RenameField(
            model_name="shiftreview",
            old_name="shift_id",
            new_name="session_id",
        ),
    ]
