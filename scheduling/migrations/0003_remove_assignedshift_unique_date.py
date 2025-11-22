from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('scheduling', '0002_process_processtask_and_more'),
    ]

    operations = [
        migrations.RunSQL(
            sql=r"""
DO $$
DECLARE
    con RECORD;
BEGIN
    -- Drop known names
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'scheduling_assignedshift_schedule_id_staff_id_shift_date_uniq') THEN
        ALTER TABLE assigned_shifts DROP CONSTRAINT scheduling_assignedshift_schedule_id_staff_id_shift_date_uniq;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'assigned_shifts_schedule_id_staff_id_shift_date_key') THEN
        ALTER TABLE assigned_shifts DROP CONSTRAINT assigned_shifts_schedule_id_staff_id_shift_date_key;
    END IF;

    -- Drop any unique constraint whose name suggests schedule_id+staff_id+shift_date
    FOR con IN
        SELECT c.conname
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE t.relname = 'assigned_shifts' AND c.contype = 'u' AND (
            c.conname ILIKE '%assignedshift%schedule_id%staff_id%shift_date%' OR
            c.conname ILIKE '%assigned_shifts%schedule_id%staff_id%shift_date%'
        )
    LOOP
        EXECUTE 'ALTER TABLE assigned_shifts DROP CONSTRAINT ' || quote_ident(con.conname);
    END LOOP;
END $$;
            """,
            reverse_sql="""-- No reverse; legacy constraint should remain removed""",
        )
    ]
