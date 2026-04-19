import uuid

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


def seed_primary_locations(apps, schema_editor):
    """
    For every existing Restaurant, create a single primary BusinessLocation
    seeded from its current lat/lng/radius/geofence_* fields so the geofence
    keeps matching on the first request after this migration runs.

    Restaurants without coordinates get a placeholder primary location with
    no coordinates; the manager can fill them in from Settings.
    """
    Restaurant = apps.get_model('accounts', 'Restaurant')
    BusinessLocation = apps.get_model('accounts', 'BusinessLocation')

    for rest in Restaurant.objects.all():
        if BusinessLocation.objects.filter(restaurant=rest).exists():
            continue
        BusinessLocation.objects.create(
            restaurant=rest,
            name=(rest.name or 'Main') + ' - Main',
            address=rest.address or '',
            latitude=rest.latitude,
            longitude=rest.longitude,
            radius=rest.radius if rest.radius is not None else 100,
            geofence_enabled=rest.geofence_enabled,
            geofence_polygon=rest.geofence_polygon or [],
            timezone='',
            is_primary=True,
            is_active=True,
        )


def drop_locations(apps, schema_editor):
    BusinessLocation = apps.get_model('accounts', 'BusinessLocation')
    BusinessLocation.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0028_rbac_role_permission_set'),
    ]

    operations = [
        migrations.CreateModel(
            name='BusinessLocation',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=120)),
                ('address', models.CharField(blank=True, default='', max_length=255)),
                ('latitude', models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True)),
                ('longitude', models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True)),
                ('radius', models.DecimalField(
                    decimal_places=2,
                    default=100,
                    max_digits=9,
                    validators=[
                        django.core.validators.MinValueValidator(5),
                        django.core.validators.MaxValueValidator(100),
                    ],
                )),
                ('geofence_enabled', models.BooleanField(default=True)),
                ('geofence_polygon', models.JSONField(blank=True, default=list)),
                ('timezone', models.CharField(blank=True, default='', max_length=50)),
                ('is_primary', models.BooleanField(default=False)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('restaurant', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='locations',
                    to='accounts.restaurant',
                )),
            ],
            options={
                'db_table': 'business_locations',
                'ordering': ['-is_primary', 'name'],
            },
        ),
        migrations.AddConstraint(
            model_name='businesslocation',
            constraint=models.UniqueConstraint(
                condition=models.Q(('is_primary', True)),
                fields=('restaurant',),
                name='unique_primary_location_per_restaurant',
            ),
        ),
        migrations.AddIndex(
            model_name='businesslocation',
            index=models.Index(fields=['restaurant', 'is_active'], name='business_lo_restaur_d06aa5_idx'),
        ),
        migrations.RunPython(seed_primary_locations, drop_locations),
    ]
