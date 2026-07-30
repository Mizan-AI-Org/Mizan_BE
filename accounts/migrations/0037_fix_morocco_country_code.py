from django.db import migrations


def fix_morocco_country_codes(apps, schema_editor):
    Restaurant = apps.get_model("accounts", "Restaurant")
    to_fix = []
    for restaurant in Restaurant.objects.all().only(
        "id",
        "country_code",
        "timezone",
        "currency",
        "phone",
        "email",
        "language",
    ).iterator():
        cc = (restaurant.country_code or "").strip()
        cc_upper = cc.upper()
        needs_fix = cc_upper in {"MY", ""} or cc.lower() == "ma"
        if not needs_fix and cc_upper == "MA":
            continue

        tz = (restaurant.timezone or "").strip()
        currency = (restaurant.currency or "").strip().upper()
        phone = restaurant.phone or ""
        email = (restaurant.email or "").strip().lower()
        language = (restaurant.language or "").strip().lower()

        looks_moroccan = (
            tz == "Africa/Casablanca"
            or "Casablanca" in tz
            or currency == "MAD"
            or "".join(ch for ch in phone if ch.isdigit()).startswith("212")
            or email.endswith(".ma")
        )

        if cc_upper == "MY" and looks_moroccan:
            to_fix.append(restaurant.id)
        elif cc.lower() == "ma":
            to_fix.append(restaurant.id)
        elif not cc and looks_moroccan:
            to_fix.append(restaurant.id)

    if to_fix:
        Restaurant.objects.filter(id__in=to_fix).update(country_code="MA")


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0036_customuser_is_platform_operator"),
    ]

    operations = [
        migrations.RunPython(fix_morocco_country_codes, migrations.RunPython.noop),
    ]
