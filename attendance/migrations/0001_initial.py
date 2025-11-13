from django.db import migrations, models
from django.conf import settings
import uuid


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ShiftReview",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)),
                ("shift_id", models.UUIDField()),
                ("rating", models.PositiveSmallIntegerField()),
                ("tags", models.JSONField(default=list, blank=True)),
                ("comments", models.TextField(blank=True, null=True)),
                ("completed_at", models.DateTimeField()),
                ("hours_decimal", models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "staff",
                    models.ForeignKey(
                        to=settings.AUTH_USER_MODEL,
                        on_delete=models.CASCADE,
                        related_name="shift_reviews",
                    ),
                ),
                (
                    "restaurant",
                    models.ForeignKey(
                        to="accounts.restaurant",
                        on_delete=models.CASCADE,
                        related_name="shift_reviews",
                        null=True,
                        blank=True,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ReviewLike",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "review",
                    models.ForeignKey(
                        to="attendance.shiftreview",
                        on_delete=models.CASCADE,
                        related_name="likes",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        to=settings.AUTH_USER_MODEL,
                        on_delete=models.CASCADE,
                        related_name="review_likes",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="reviewlike",
            constraint=models.UniqueConstraint(fields=["review", "user"], name="unique_review_user_like"),
        ),
    ]