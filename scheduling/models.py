from django.db import models
import uuid

class ScheduleTemplate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.name} - {self.restaurant.name}"

class TemplateShift(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.ForeignKey(ScheduleTemplate, on_delete=models.CASCADE, related_name='shifts')
    role = models.CharField(max_length=20, choices=[
        ('server', 'Server'), ('chef', 'Chef'), ('cleaner', 'Cleaner')
    ])
    day_of_week = models.IntegerField(choices=[(0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'),
                                              (3, 'Thursday'), (4, 'Friday'), (5, 'Saturday'), (6, 'Sunday')])
    start_time = models.TimeField()
    end_time = models.TimeField()
    required_staff = models.IntegerField(default=1)
    
    class Meta:
        unique_together = ['template', 'role', 'day_of_week']
    
    def __str__(self):
        return f"{self.get_day_of_week_display()} - {self.role}"

class WeeklySchedule(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE)
    week_start = models.DateField()
    week_end = models.DateField()
    is_published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['restaurant', 'week_start']
    
    def __str__(self):
        return f"Week of {self.week_start} - {self.restaurant.name}"
    