"""
Trigger Miya to send a clock-in reminder to a specific phone number.
POSTs the clock_in_reminder event to the Lua user-events webhook; Miya then sends the WhatsApp.
Usage:
  python manage.py send_clock_in_reminder_now 2203736808
  python manage.py send_clock_in_reminder_now 2203736808 --name "Salima" --time "15:00" --location "Ima Restaurant"
"""
from django.core.management.base import BaseCommand
from notifications.services import notification_service


class Command(BaseCommand):
    help = "Trigger Miya to send a clock-in reminder to the given phone number (POSTs to user-events webhook)."

    def add_arguments(self, parser):
        parser.add_argument(
            "phone",
            type=str,
            help="Phone number to receive the reminder (e.g. 2203736808 or +2203736808)",
        )
        parser.add_argument(
            "--name",
            type=str,
            default="Team Member",
            help="Staff first name for the message (default: Team Member)",
        )
        parser.add_argument(
            "--time",
            type=str,
            default="",
            help="Shift start time string (e.g. 15:00)",
        )
        parser.add_argument(
            "--minutes",
            type=str,
            default="10 minutes",
            help="Minutes until shift (e.g. 10 minutes)",
        )
        parser.add_argument(
            "--location",
            type=str,
            default="Restaurant",
            help="Location name for the reminder",
        )

    def handle(self, *args, **options):
        phone = (options["phone"] or "").strip()
        if not phone:
            self.stderr.write(self.style.ERROR("Phone number is required."))
            return

        first_name = (options["name"] or "Team Member").strip()
        start_time_str = (options["time"] or "").strip()
        minutes_until_str = (options["minutes"] or "10 minutes").strip()
        location = (options["location"] or "Restaurant").strip()

        self.stdout.write(
            f"Sending clock_in_reminder event to Miya for {phone} (name={first_name}, time={start_time_str or '(default)'}, location={location})..."
        )
        ok, result = notification_service.send_lua_clock_in_reminder(
            phone=phone,
            first_name=first_name,
            start_time_str=start_time_str or "soon",
            minutes_until_str=minutes_until_str,
            location=location,
            shift_id=None,
            template_name=None,
        )
        if ok:
            self.stdout.write(self.style.SUCCESS("Miya was notified. You should receive the clock-in reminder on WhatsApp shortly."))
        else:
            self.stderr.write(self.style.ERROR(f"Failed: {result}"))
            self.stderr.write(
                "Ensure LUA_USER_EVENTS_WEBHOOK or LUA_AGENT_ID and LUA_API_KEY are set in .env."
            )
