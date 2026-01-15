import os
import django
import json
from datetime import date

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mizan.settings')
django.setup()

from scheduling.services import OptimizationService
from accounts.models import Restaurant

def debug_optimization():
    try:
        # Get Barometre restaurant
        # We can look it up by name or ID if we have it. 
        # From previous output: 1711f466-64ad-4153-b0de-07604656be49
        # But let's be safe and find by name
        restaurant = Restaurant.objects.filter(name__icontains="Barometre").first()
        if not restaurant:
            print("Error: Barometre restaurant not found")
            return

        print(f"Testing Optimization for: {restaurant.name} ({restaurant.id})")
        
        # Optimize for next week
        week_start = "2025-12-01"
        department = "kitchen" # or 'all'
        
        print(f"Calling OptimizationService.optimize_schedule(week_start={week_start}, department={department})")
        
        # Clean up existing shifts for this week to allow regeneration
        from scheduling.models import AssignedShift
        print("Cleaning up existing shifts...")
        AssignedShift.objects.filter(
            schedule__restaurant=restaurant,
            shift_date__gte=week_start
        ).delete()

        result = OptimizationService.optimize_schedule(
            str(restaurant.id),
            week_start,
            department
        )
        
        print("\n--- Result ---")
        print(json.dumps(result, indent=2, default=str))
        
        # Check created shifts in DB
        print("\n--- Created Shifts in DB ---")
        shifts = AssignedShift.objects.filter(
            schedule__restaurant=restaurant,
            shift_date__gte=week_start
        ).order_by('shift_date', 'start_time')
        
        for shift in shifts:
            print(f"Shift ID: {shift.id}")
            print(f"  Staff: {shift.staff.get_full_name()}")
            print(f"  Role: {shift.role}")
            print(f"  Date: {shift.shift_date}")
            print(f"  Notes (Title): {shift.notes}")
            print(f"  Color: {shift.color}")
            print("-" * 20)

        # Check for John Doe
        result_str = json.dumps(result, default=str)
        if "John Doe" in result_str:
            print("\n!!! FOUND 'John Doe' in output !!!")
        else:
            print("\nNo 'John Doe' found in output.")
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Exception: {e}")

if __name__ == "__main__":
    debug_optimization()
