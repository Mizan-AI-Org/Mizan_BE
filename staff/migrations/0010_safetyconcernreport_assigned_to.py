from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('staff', '0009_rename_staff_staffr_restaur_5cf08b_idx_staff_staff_restaur_711676_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='safetyconcernreport',
            name='assigned_to',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='assigned_concerns',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
