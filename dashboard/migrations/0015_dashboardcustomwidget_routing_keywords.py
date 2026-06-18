"""Add routing_keywords to custom dashboard widgets."""

from django.db import migrations, models


def backfill_routing_keywords(apps, schema_editor):
    DashboardCustomWidget = apps.get_model("dashboard", "DashboardCustomWidget")
    stop = frozenset(
        {
            "event",
            "events",
            "widget",
            "team",
            "staff",
            "dashboard",
            "lane",
        }
    )
    for widget in DashboardCustomWidget.objects.all().iterator():
        if widget.routing_keywords:
            continue
        tokens = []
        for part in (widget.title or "", widget.subtitle or ""):
            for word in part.lower().split():
                w = "".join(ch for ch in word if ch.isalnum())
                if len(w) >= 3 and w not in stop:
                    tokens.append(w)
        if tokens:
            widget.routing_keywords = list(dict.fromkeys(tokens))[:10]
            widget.save(update_fields=["routing_keywords"])


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0014_remove_task_dashboard_t_rest_cu_status_idx"),
    ]

    operations = [
        migrations.AddField(
            model_name="dashboardcustomwidget",
            name="routing_keywords",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(backfill_routing_keywords, migrations.RunPython.noop),
    ]
