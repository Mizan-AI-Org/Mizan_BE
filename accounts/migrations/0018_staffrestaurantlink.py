# Generated manually for multi-restaurant staff identity

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0017_staff_activation_batch_invited_by'),
    ]

    operations = [
        migrations.CreateModel(
            name='StaffRestaurantLink',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('role', models.CharField(default='WAITER', max_length=20)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('restaurant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='staff_links', to='accounts.restaurant')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='restaurant_links', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'staff_restaurant_links',
                'ordering': ['restaurant__name'],
                'unique_together': {('user', 'restaurant')},
            },
        ),
        migrations.AddIndex(
            model_name='staffrestaurantlink',
            index=models.Index(fields=['restaurant', 'is_active'], name='staff_resta_restaur_idx'),
        ),
        migrations.AddIndex(
            model_name='staffrestaurantlink',
            index=models.Index(fields=['user', 'is_active'], name='staff_resta_user_id_idx'),
        ),
    ]
