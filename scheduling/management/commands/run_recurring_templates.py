from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from scheduling.recurrence_service import RecurrenceService


class Command(BaseCommand):
    help = "Generate tasks from active templates based on frequency. Intended to be run daily via cron or scheduler."

    def add_arguments(self, parser):
        parser.add_argument('--date', type=str, help='Override date (YYYY-MM-DD). Defaults to today.', default=None)
        parser.add_argument('--frequency', type=str, help='Limit to frequency (DAILY/WEEKLY/MONTHLY/QUARTERLY/ANNUALLY/CUSTOM).', default=None)
        parser.add_argument('--restaurant-id', type=str, help='Limit to a specific restaurant UUID.', default=None)

    def handle(self, *args, **options):
        date_str = options.get('date')
        freq = options.get('frequency')
        restaurant_id = options.get('restaurant_id')

        date = None
        if date_str:
            try:
                date = timezone.datetime.strptime(date_str, '%Y-%m-%d').date()
            except Exception as e:
                raise CommandError(f"Invalid --date format: {e}")

        restaurant = None
        if restaurant_id:
            from accounts.models import Restaurant
            try:
                restaurant = Restaurant.objects.get(id=restaurant_id)
            except Restaurant.DoesNotExist:
                raise CommandError(f"Restaurant not found: {restaurant_id}")

        results = RecurrenceService.generate(date=date, frequency=freq, restaurant=restaurant)

        self.stdout.write(self.style.SUCCESS(
            f"Recurrence run complete: date={results['date']} frequency={results['frequency']} "
            f"templates_considered={results['templates_considered']} generated={results['templates_generated']} "
            f"tasks_created={results['tasks_created']}"
        ))

        if results['errors']:
            self.stdout.write(self.style.WARNING(f"Errors: {results['errors']}"))