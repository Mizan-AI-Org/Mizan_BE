"""
AI-Powered Scheduling Service
Generates optimal shift schedules based on demand forecasts, staff availability, and constraints
"""
from datetime import datetime, timedelta, time
from typing import List, Dict, Tuple, Optional
from django.db.models import Avg, Sum, Count, Q
from django.utils import timezone
import logging

from .models import AssignedShift, WeeklySchedule, ShiftTask, ScheduleTemplate, TemplateShift
from accounts.models import CustomUser, Restaurant

logger = logging.getLogger(__name__)


class AIScheduler:
    """
    AI-powered scheduling engine that considers:
    - Historical sales data and demand forecasts
    - Staff availability and preferences
    - Labor cost targets
    - Required roles per shift
    - Labor law constraints (rest periods, max hours)
    """
    
    # Labor law constraints
    MIN_REST_HOURS = 11  # Minimum hours between shifts
    MAX_WEEKLY_HOURS = 48  # Maximum hours per week
    MAX_DAILY_HOURS = 12  # Maximum hours per day
    
    def __init__(self, restaurant: Restaurant):
        self.restaurant = restaurant
    
    def generate_optimal_schedule(
        self,
        week_start: datetime.date,
        template_id: Optional[str] = None,
        demand_forecast: Optional[Dict] = None,
        labor_budget: Optional[float] = None,
        demand_level: str = "MEDIUM"
    ) -> Dict:
        """
        Generate optimal schedule for a week
        """
        logger.info(f"Generating optimal schedule for {self.restaurant.name} starting {week_start}")
        
        # Get demand forecast
        if not demand_forecast:
            demand_forecast = self._get_demand_forecast(week_start)
        
        # Override with global demand_level if forecast is empty for some reason
        if not demand_forecast:
            demand_forecast = {d: demand_level for d in ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']}
            
        # Get available staff
        available_staff = self._get_available_staff()
        
        # Get template if provided
        template = None
        if template_id:
            try:
                template = ScheduleTemplate.objects.get(id=template_id, restaurant=self.restaurant)
            except (ScheduleTemplate.DoesNotExist, ValueError):
                logger.warning(f"Template {template_id} not found for restaurant {self.restaurant.id}")

        # Get historical shift patterns (fallback if no template)
        historical_patterns = self._get_historical_patterns(week_start) if not template else {}
        
        # Generate shifts
        generated_shifts = []
        total_hours = 0
        estimated_cost = 0
        warnings = []
        
        # For each day of the week
        for day_offset in range(7):
            shift_date = week_start + timedelta(days=day_offset)
            day_name = shift_date.strftime('%A')
            day_num = shift_date.weekday() # 0=Monday
            
            # Get demand for this day
            current_day_demand = demand_forecast.get(day_name, demand_level)
            
            if template:
                # Use template shifts for this day
                day_template_shifts = TemplateShift.objects.filter(template=template, day_of_week=day_num)
                required_roles = {}
                # Calculate required roles based on template + demand scaling
                scale = {'LOW': 0.7, 'MEDIUM': 1.0, 'HIGH': 1.3}.get(current_day_demand, 1.0)
                
                for ts in day_template_shifts:
                    required_roles[ts.role] = max(1, round(ts.required_staff * scale))
            else:
                # Determine required staff by role from history
                required_roles = self._calculate_required_roles(current_day_demand, historical_patterns)
            
            # Assign staff to shifts
            day_shifts = self._assign_staff_to_shifts(
                shift_date=shift_date,
                required_roles=required_roles,
                available_staff=available_staff,
                existing_assignments=generated_shifts,
                template=template if template else None
            )
            
            generated_shifts.extend(day_shifts)
            
            # Calculate hours and cost
            for shift in day_shifts:
                hours = shift['hours']
                total_hours += hours
                estimated_cost += hours * shift['hourly_rate']
        
        # Validate constraints
        constraint_warnings = self._validate_constraints(generated_shifts)
        warnings.extend(constraint_warnings)
        
        # Calculate coverage score
        coverage_score = self._calculate_coverage_score(generated_shifts, demand_forecast)
        
        return {
            'shifts': generated_shifts,
            'total_hours': round(total_hours, 2),
            'estimated_cost': round(estimated_cost, 2),
            'coverage_score': round(coverage_score, 2),
            'warnings': warnings,
            'demand_forecast': demand_forecast
        }
    
    def _get_demand_forecast(self, week_start: datetime.date) -> Dict:
        """
        Get demand forecast for the week based on historical data
        
        Returns:
            {
                'Monday': 'HIGH',
                'Tuesday': 'MEDIUM',
                ...
            }
        """
        # Get historical sales data for same week in previous years
        from pos.models import Order
        
        forecast = {}
        
        for day_offset in range(7):
            date = week_start + timedelta(days=day_offset)
            day_name = date.strftime('%A')
            
            # Get historical data for this day of week
            historical_dates = []
            for weeks_back in range(1, 13):  # Last 12 weeks
                hist_date = date - timedelta(weeks=weeks_back)
                historical_dates.append(hist_date)
            
            # Calculate average orders for this day
            avg_orders = Order.objects.filter(
                restaurant=self.restaurant,
                order_time__date__in=historical_dates,
                status='COMPLETED'
            ).count() / len(historical_dates) if historical_dates else 0
            
            # Classify demand level
            if avg_orders > 50:
                demand_level = 'HIGH'
            elif avg_orders > 25:
                demand_level = 'MEDIUM'
            else:
                demand_level = 'LOW'
            
            forecast[day_name] = demand_level
        
        return forecast
    
    def _get_available_staff(self) -> List[CustomUser]:
        """Get all active staff members"""
        return list(CustomUser.objects.filter(
            restaurant=self.restaurant,
            is_active=True
        ).select_related('profile'))
    
    def _get_historical_patterns(self, week_start: datetime.date) -> Dict:
        """
        Analyze historical shift patterns
        
        Returns:
            {
                'CHEF': {'avg_per_shift': 2, 'peak_hours': [(11, 14), (18, 22)]},
                'WAITER': {'avg_per_shift': 3, 'peak_hours': [(11, 14), (18, 22)]},
                ...
            }
        """
        patterns = {}
        
        # Get shifts from last 4 weeks
        four_weeks_ago = week_start - timedelta(weeks=4)
        
        historical_shifts = AssignedShift.objects.filter(
            schedule__restaurant=self.restaurant,
            shift_date__gte=four_weeks_ago,
            shift_date__lt=week_start,
            status__in=['COMPLETED', 'CONFIRMED']
        )
        
        # Analyze by role
        for role_code, role_name in self.restaurant.staff.values_list('role', flat=True).distinct():
            role_shifts = historical_shifts.filter(role=role_code)
            
            if role_shifts.exists():
                avg_per_day = role_shifts.values('shift_date').annotate(
                    count=Count('id')
                ).aggregate(avg=Avg('count'))['avg'] or 1
                
                patterns[role_code] = {
                    'avg_per_shift': max(1, round(avg_per_day)),
                    'peak_hours': self._identify_peak_hours(role_shifts)
                }
        
        return patterns
    
    def _identify_peak_hours(self, shifts) -> List[Tuple[int, int]]:
        """Identify peak hours from historical shifts"""
        # Simplified: return common restaurant peak hours
        return [(11, 14), (18, 22)]  # Lunch and dinner
    
    def _calculate_required_roles(self, demand_level: str, historical_patterns: Dict) -> Dict:
        """
        Calculate required staff by role based on demand
        
        Returns:
            {
                'CHEF': 2,
                'WAITER': 3,
                'CASHIER': 1
            }
        """
        multipliers = {
            'LOW': 0.7,
            'MEDIUM': 1.0,
            'HIGH': 1.3
        }
        
        multiplier = multipliers.get(demand_level, 1.0)
        
        required = {}
        for role, pattern in historical_patterns.items():
            base_count = pattern['avg_per_shift']
            required[role] = max(1, round(base_count * multiplier))
        
        # Ensure minimum coverage
        if 'CHEF' not in required:
            required['CHEF'] = 1
        if 'WAITER' not in required:
            required['WAITER'] = 2
        
        return required
    
    def _assign_staff_to_shifts(
        self,
        shift_date: datetime.date,
        required_roles: Dict,
        available_staff: List[CustomUser],
        existing_assignments: List[Dict],
        template: Optional[ScheduleTemplate] = None
    ) -> List[Dict]:
        """
        Assign staff to shifts for a specific day
        
        Returns:
            List of shift assignments
        """
        shifts = []
        
        # Get staff already assigned this week
        weekly_hours = self._calculate_weekly_hours(existing_assignments)
        
        for role, count in required_roles.items():
            # Find available staff for this role
            role_staff = [s for s in available_staff if s.role == role]
            
            # Sort by weekly hours (assign to those with fewer hours first)
            role_staff.sort(key=lambda s: weekly_hours.get(str(s.id), 0))
            
            assigned_count = 0
            for staff in role_staff:
                if assigned_count >= count:
                    break
                
                # Check constraints
                if not self._can_assign_shift(staff, shift_date, existing_assignments):
                    continue
                
                # Determine shift times based on role and demand
                start_time, end_time = self._determine_shift_times(role, shift_date, template)
                
                # Calculate hours
                shift_duration = self._calculate_shift_duration(start_time, end_time)
                
                # Get hourly rate
                hourly_rate = staff.profile.hourly_rate if hasattr(staff, 'profile') else 15.0
                
                shifts.append({
                    'staff_id': str(staff.id),
                    'staff_name': f"{staff.first_name} {staff.last_name}",
                    'role': role,
                    'shift_date': shift_date,
                    'start_time': start_time,
                    'end_time': end_time,
                    'hours': shift_duration,
                    'hourly_rate': hourly_rate
                })
                
                assigned_count += 1
                weekly_hours[str(staff.id)] = weekly_hours.get(str(staff.id), 0) + shift_duration
        
        return shifts
    
    def _calculate_weekly_hours(self, assignments: List[Dict]) -> Dict[str, float]:
        """Calculate total hours per staff member"""
        hours = {}
        for assignment in assignments:
            staff_id = assignment['staff_id']
            hours[staff_id] = hours.get(staff_id, 0) + assignment['hours']
        return hours
    
    def _can_assign_shift(
        self,
        staff: CustomUser,
        shift_date: datetime.date,
        existing_assignments: List[Dict]
    ) -> bool:
        """Check if staff can be assigned to this shift"""
        staff_id = str(staff.id)
        
        # Check weekly hours limit
        weekly_hours = sum(
            a['hours'] for a in existing_assignments
            if a['staff_id'] == staff_id
        )
        
        if weekly_hours >= self.MAX_WEEKLY_HOURS:
            return False
        
        # Check rest period between shifts
        staff_assignments = [a for a in existing_assignments if a['staff_id'] == staff_id]
        
        for assignment in staff_assignments:
            prev_date = assignment['shift_date']
            prev_end = assignment['end_time']
            
            # Calculate hours between shifts
            if prev_date == shift_date:
                # Same day - not allowed
                return False
            
            if prev_date == shift_date - timedelta(days=1):
                # Previous day - check rest period
                hours_between = 24 - prev_end.hour
                if hours_between < self.MIN_REST_HOURS:
                    return False
        
        return True
    
    def _determine_shift_times(self, role: str, shift_date: datetime.date, template: Optional[ScheduleTemplate] = None) -> Tuple[time, time]:
        """Determine shift start and end times based on role or template"""
        if template:
            day_num = shift_date.weekday()
            ts = TemplateShift.objects.filter(template=template, role=role, day_of_week=day_num).first()
            if ts:
                return ts.start_time, ts.end_time

        # Simplified shift times fallback
        shift_templates = {
            'CHEF': (time(10, 0), time(18, 0)),  # 8-hour shift
            'WAITER': (time(11, 0), time(19, 0)),  # 8-hour shift
            'CASHIER': (time(11, 0), time(19, 0)),  # 8-hour shift
            'CLEANER': (time(6, 0), time(14, 0)),  # Morning shift
        }
        
        return shift_templates.get(role, (time(9, 0), time(17, 0)))
    
    def _calculate_shift_duration(self, start_time: time, end_time: time) -> float:
        """Calculate shift duration in hours"""
        start_dt = datetime.combine(datetime.today(), start_time)
        end_dt = datetime.combine(datetime.today(), end_time)
        
        if end_dt < start_dt:
            end_dt += timedelta(days=1)
        
        duration = (end_dt - start_dt).total_seconds() / 3600
        
        # Subtract break time (30 minutes)
        return duration - 0.5
    
    def _validate_constraints(self, shifts: List[Dict]) -> List[str]:
        """Validate labor law constraints"""
        warnings = []
        
        # Check weekly hours per staff
        weekly_hours = self._calculate_weekly_hours(shifts)
        
        for staff_id, hours in weekly_hours.items():
            if hours > self.MAX_WEEKLY_HOURS:
                warnings.append(f"Staff {staff_id} exceeds max weekly hours: {hours:.1f}h")
        
        # Check daily hours
        daily_hours = {}
        for shift in shifts:
            key = (shift['staff_id'], shift['shift_date'])
            daily_hours[key] = daily_hours.get(key, 0) + shift['hours']
        
        for (staff_id, date), hours in daily_hours.items():
            if hours > self.MAX_DAILY_HOURS:
                warnings.append(f"Staff {staff_id} exceeds max daily hours on {date}: {hours:.1f}h")
        
        return warnings
    
    def _calculate_coverage_score(self, shifts: List[Dict], demand_forecast: Dict) -> float:
        """
        Calculate how well the schedule covers demand
        
        Returns:
            Score from 0-100
        """
        if not shifts:
            return 0.0
        
        # Count shifts per day
        shifts_per_day = {}
        for shift in shifts:
            date = shift['shift_date']
            shifts_per_day[date] = shifts_per_day.get(date, 0) + 1
        
        # Compare with demand
        total_score = 0
        for date, count in shifts_per_day.items():
            day_name = date.strftime('%A')
            demand = demand_forecast.get(day_name, 'MEDIUM')
            
            # Expected shifts based on demand
            expected = {'LOW': 3, 'MEDIUM': 5, 'HIGH': 7}[demand]
            
            # Calculate score (100% if meets expected, lower if under/over)
            if count >= expected:
                score = 100
            else:
                score = (count / expected) * 100
            
            total_score += score
        
        return total_score / len(shifts_per_day) if shifts_per_day else 0.0
    
    def suggest_task_assignments(self, shift: AssignedShift) -> List[Dict]:
        """
        Suggest tasks for a shift based on role and time
        
        Returns:
            List of suggested tasks
        """
        suggestions = []
        
        role_tasks = {
            'CHEF': [
                {'title': 'Prep ingredients', 'priority': 'HIGH', 'duration': 60},
                {'title': 'Cook orders', 'priority': 'HIGH', 'duration': 240},
                {'title': 'Clean kitchen', 'priority': 'MEDIUM', 'duration': 30},
                {'title': 'Check inventory', 'priority': 'MEDIUM', 'duration': 15},
            ],
            'WAITER': [
                {'title': 'Set up tables', 'priority': 'HIGH', 'duration': 30},
                {'title': 'Take orders', 'priority': 'HIGH', 'duration': 180},
                {'title': 'Serve food', 'priority': 'HIGH', 'duration': 180},
                {'title': 'Clean tables', 'priority': 'MEDIUM', 'duration': 60},
            ],
            'CLEANER': [
                {'title': 'Clean dining area', 'priority': 'HIGH', 'duration': 60},
                {'title': 'Clean restrooms', 'priority': 'HIGH', 'duration': 30},
                {'title': 'Take out trash', 'priority': 'MEDIUM', 'duration': 15},
                {'title': 'Restock supplies', 'priority': 'LOW', 'duration': 20},
            ],
            'CASHIER': [
                {'title': 'Open register', 'priority': 'HIGH', 'duration': 10},
                {'title': 'Process payments', 'priority': 'HIGH', 'duration': 240},
                {'title': 'Balance register', 'priority': 'HIGH', 'duration': 20},
                {'title': 'Handle customer inquiries', 'priority': 'MEDIUM', 'duration': 60},
            ],
        }
        
        tasks = role_tasks.get(shift.role, [])
        
        for task in tasks:
            suggestions.append({
                'title': task['title'],
                'priority': task['priority'],
                'estimated_duration': timedelta(minutes=task['duration']),
                'shift_id': str(shift.id)
            })
        
        return suggestions