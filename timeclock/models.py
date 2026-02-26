from django.db import models
from django.conf import settings
import uuid


class ClockEvent(models.Model):
    EVENT_TYPES = [
        ('in', 'Clock In'),
        ('out', 'Clock Out'),
        ('break_start', 'Break Start'),
        ('break_end', 'Break End'),
    ]
    CLOCK_IN_METHOD_OVERRIDE = 'manager_override'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    staff = models.ForeignKey('accounts.CustomUser', on_delete=models.CASCADE, related_name='clock_events')
    event_type = models.CharField(max_length=20, choices=EVENT_TYPES)
    timestamp = models.DateTimeField(auto_now_add=True)
    photo = models.ImageField(upload_to='clock_photos/', null=True, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    device_id = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    location_encrypted = models.TextField(db_column='location_encrypted', blank=True, default='')
    # Manager override: when set, clock-in was performed by a manager (exception to geofence)
    performed_by = models.ForeignKey(
        'accounts.CustomUser', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='clock_events_performed'
    )
    override_reason = models.TextField(blank=True)
    class Meta:
        ordering = ['-timestamp']
    
    def __str__(self):
        return f"{self.staff.username} - {self.event_type} - {self.timestamp}"


class CashSession(models.Model):
    """Cash drawer reconciliation per shift. Staff count cash at end of shift;
    system compares against POS cash sales to detect variance."""
    STATUS_CHOICES = [
        ('OPEN', 'Open'),
        ('COUNTED', 'Counted'),
        ('VERIFIED', 'Verified by Manager'),
        ('FLAGGED', 'Flagged — Variance'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='cash_sessions')
    shift = models.ForeignKey('scheduling.AssignedShift', on_delete=models.SET_NULL, null=True, blank=True, related_name='cash_sessions')
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='cash_sessions')

    opening_float = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Cash in drawer at shift start (MAD)")
    expected_cash = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="POS-reported cash sales for the shift")
    counted_cash = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Staff-reported cash in drawer at shift end")
    variance = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="counted - (opening_float + expected_cash)")
    variance_reason = models.TextField(blank=True, help_text="Staff explanation for variance")
    photo = models.ImageField(upload_to='cash_counts/', null=True, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='OPEN')
    manager_notes = models.TextField(blank=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='verified_cash_sessions')

    opened_at = models.DateTimeField(auto_now_add=True)
    counted_at = models.DateTimeField(null=True, blank=True)
    session_date = models.DateField(help_text="Date of the cash session")

    class Meta:
        db_table = 'cash_sessions'
        ordering = ['-session_date', '-opened_at']
        indexes = [
            models.Index(fields=['restaurant', 'session_date']),
            models.Index(fields=['staff', 'session_date']),
        ]

    def compute_variance(self):
        if self.counted_cash is not None and self.expected_cash is not None:
            self.variance = self.counted_cash - (self.opening_float + self.expected_cash)

    def __str__(self):
        return f"Cash {self.session_date} - {self.staff} ({self.status})"