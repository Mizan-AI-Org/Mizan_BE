"""
AI Assistant Service - Handles intelligent recommendations and insights
"""
import os
import json
from datetime import date, timedelta
from django.db.models import Sum, Avg, Count
from django.utils import timezone


class AIAssistantService:
    """
    Service for AI-powered insights and recommendations
    """
    
    def __init__(self, restaurant=None, provider='GROQ'):
        self.restaurant = restaurant
        self.provider = provider
        self.api_key = os.getenv('GROQ_API_KEY', '')
    
    def get_restaurant_context(self):
        """Get restaurant context for AI"""
        from accounts.models import Restaurant
        from scheduling.models import AssignedShift, ShiftTask
        from dashboard.models import DailyKPI
        
        if not self.restaurant:
            return {}
        
        # Get recent KPI data
        last_7_days = date.today() - timedelta(days=7)
        kpis = DailyKPI.objects.filter(
            restaurant=self.restaurant,
            date__gte=last_7_days
        )
        
        # Get staffing info
        today_shifts = AssignedShift.objects.filter(
            schedule__restaurant=self.restaurant,
            shift_date=date.today()
        ).count()
        
        # Get pending tasks
        pending_tasks = ShiftTask.objects.filter(
            shift__schedule__restaurant=self.restaurant,
            status='TODO'
        ).count()
        
        return {
            'restaurant_name': self.restaurant.name,
            'timezone': self.restaurant.timezone,
            'operating_hours': self.restaurant.operating_hours,
            'recent_revenue': float(kpis.aggregate(Sum('total_revenue'))['total_revenue__sum'] or 0),
            'avg_daily_orders': float(kpis.aggregate(Avg('total_orders'))['total_orders__avg'] or 0),
            'today_staff_count': today_shifts,
            'pending_tasks': pending_tasks,
            'currency': self.restaurant.currency
        }
    
    def generate_insights(self):
        """Generate AI insights based on restaurant data"""
        from dashboard.models import DailyKPI
        
        insights = []
        
        if not self.restaurant:
            return insights
        
        last_30_days = date.today() - timedelta(days=30)
        kpis = DailyKPI.objects.filter(
            restaurant=self.restaurant,
            date__gte=last_30_days
        ).order_by('date')
        
        if not kpis.exists():
            return [{
                'type': 'INFO',
                'title': 'Getting Started',
                'message': 'Start tracking KPIs to get AI insights',
                'priority': 'LOW'
            }]
        
        # Revenue Analysis
        revenues = list(kpis.values_list('total_revenue', flat=True))
        avg_revenue = sum(revenues) / len(revenues) if revenues else 0
        latest_revenue = kpis.last().total_revenue
        
        if latest_revenue > avg_revenue * 1.3:
            insights.append({
                'type': 'SUCCESS',
                'title': '📈 Exceptional Revenue Performance',
                'message': f'Revenue is {((latest_revenue/avg_revenue - 1) * 100):.1f}% above 30-day average!',
                'recommendation': 'Continue current staffing and menu strategies',
                'priority': 'MEDIUM'
            })
        elif latest_revenue < avg_revenue * 0.7:
            insights.append({
                'type': 'WARNING',
                'title': '📉 Revenue Below Average',
                'message': f'Revenue dropped to {((latest_revenue/avg_revenue - 1) * 100):.1f}% below average',
                'recommendation': 'Consider promotional activities or menu adjustments',
                'priority': 'HIGH'
            })
        
        # Labor Cost Analysis
        labor_costs = list(kpis.values_list('labor_cost_percentage', flat=True))
        avg_labor = sum(labor_costs) / len(labor_costs) if labor_costs else 0
        latest_labor = kpis.last().labor_cost_percentage
        
        if latest_labor > 35:
            insights.append({
                'type': 'WARNING',
                'title': '⚠️ High Labor Costs',
                'message': f'Labor costs at {latest_labor:.1f}% - Industry average is 28-35%',
                'recommendation': 'Review staffing levels during slow hours',
                'priority': 'HIGH'
            })
        
        # Food Waste Analysis
        waste_costs = list(kpis.values_list('food_waste_cost', flat=True))
        avg_waste = sum(waste_costs) / len(waste_costs) if waste_costs else 0
        latest_waste = kpis.last().food_waste_cost
        
        if latest_waste > avg_waste * 1.2:
            insights.append({
                'type': 'WARNING',
                'title': '🗑️ Increased Food Waste',
                'message': f'Food waste at ${latest_waste:.2f} - {((latest_waste/avg_waste - 1) * 100):.1f}% above average',
                'recommendation': 'Review portion sizes and prep procedures',
                'priority': 'MEDIUM'
            })
        
        # Inventory Analysis
        stockouts = kpis.aggregate(Sum('revenue_lost_to_stockouts'))['revenue_lost_to_stockouts__sum'] or 0
        if stockouts > 0:
            insights.append({
                'type': 'WARNING',
                'title': '📦 Inventory Issues',
                'message': f'${stockouts:.2f} revenue lost to stockouts',
                'recommendation': 'Improve inventory management and supplier coordination',
                'priority': 'HIGH'
            })
        
        # Order Average Analysis
        avg_orders = kpis.aggregate(Avg('avg_order_value'))['avg_order_value__avg'] or 0
        order_count = kpis.aggregate(Sum('total_orders'))['total_orders__sum'] or 0
        if avg_orders > 0:
            insights.append({
                'type': 'INFO',
                'title': '💰 Performance Summary',
                'message': f'{int(order_count)} orders processed with average ticket: ${avg_orders:.2f}',
                'recommendation': 'Analyze top-selling items for promotion',
                'priority': 'LOW'
            })
        
        return insights
    
    def get_scheduling_recommendations(self):
        """Get AI recommendations for staff scheduling"""
        from scheduling.models import AssignedShift
        from accounts.models import CustomUser
        
        recommendations = []
        
        if not self.restaurant:
            return recommendations
        
        # Get staffing patterns for past 30 days
        last_30_days = date.today() - timedelta(days=30)
        shifts = AssignedShift.objects.filter(
            schedule__restaurant=self.restaurant,
            shift_date__gte=last_30_days
        )
        
        # Analyze by day of week
        day_staff_needs = {}
        for shift in shifts:
            day = shift.shift_date.weekday()
            if day not in day_staff_needs:
                day_staff_needs[day] = []
            day_staff_needs[day].append(shift)
        
        days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        for day_num, shifts_list in day_staff_needs.items():
            avg_staff = len(shifts_list) / (30 / 7)  # Average staff for this day
            
            if day_num >= 4:  # Weekend
                if avg_staff < 8:
                    recommendations.append({
                        'day': days[day_num],
                        'type': 'UNDERSTAFFED',
                        'message': f'{days[day_num]} typically needs {avg_staff:.0f} staff but pattern shows lower numbers',
                        'action': 'Consider hiring or scheduling more staff for weekends'
                    })
        
        return recommendations
    
    def generate_task_suggestions(self):
        """Generate AI suggestions for daily tasks"""
        from scheduling.models import TaskCategory, ShiftTask
        from datetime import datetime
        
        suggestions = []
        
        if not self.restaurant:
            return suggestions
        
        # Get task categories
        categories = TaskCategory.objects.filter(restaurant=self.restaurant)
        
        # Get today's incomplete tasks
        today_tasks = ShiftTask.objects.filter(
            shift__schedule__restaurant=self.restaurant,
            shift__shift_date=date.today(),
            status__in=['TODO', 'IN_PROGRESS']
        )
        
        # By default suggest common tasks if none exist
        if not today_tasks.exists():
            common_tasks = [
                {'title': 'Pre-service briefing', 'priority': 'HIGH', 'category': 'Operations'},
                {'title': 'Inventory check', 'priority': 'MEDIUM', 'category': 'Inventory'},
                {'title': 'Equipment maintenance check', 'priority': 'MEDIUM', 'category': 'Maintenance'},
                {'title': 'Daily cleaning checklist', 'priority': 'HIGH', 'category': 'Cleaning'},
                {'title': 'Staff break schedule review', 'priority': 'LOW', 'category': 'HR'},
            ]
            suggestions = common_tasks
        
        return suggestions
    
    def get_cost_optimization_tips(self):
        """Get AI tips for cost optimization"""
        from dashboard.models import DailyKPI
        
        tips = []
        
        if not self.restaurant:
            return tips
        
        # Get recent KPI
        try:
            today_kpi = DailyKPI.objects.get(
                restaurant=self.restaurant,
                date=date.today()
            )
        except:
            return tips
        
        # Analyze labor cost
        if today_kpi.labor_cost_percentage > 35:
            tips.append({
                'area': 'Labor Cost',
                'tip': 'Consider staggered scheduling during off-peak hours',
                'potential_savings': f'${today_kpi.total_revenue * 0.05:.2f}',
                'difficulty': 'MEDIUM'
            })
        
        # Analyze food waste
        if today_kpi.food_waste_cost > 100:
            tips.append({
                'area': 'Food Waste',
                'tip': 'Implement portion control and staff training on proper handling',
                'potential_savings': f'${today_kpi.food_waste_cost * 0.3:.2f}',
                'difficulty': 'LOW'
            })
        
        # Analyze inventory
        if today_kpi.revenue_lost_to_stockouts > 0:
            tips.append({
                'area': 'Inventory',
                'tip': 'Improve supplier relationships to reduce stockout periods',
                'potential_savings': f'${today_kpi.revenue_lost_to_stockouts:.2f}',
                'difficulty': 'HIGH'
            })
        
        return tips


class TaskManagementAI:
    """AI for intelligent task management"""
    
    def __init__(self, restaurant):
        self.restaurant = restaurant
    
    def prioritize_tasks(self, tasks):
        """AI-prioritize tasks based on various factors"""
        from scheduling.models import ShiftTask
        
        prioritized = []
        
        for task in tasks:
            score = 0
            
            # Priority weight
            priority_weights = {'URGENT': 10, 'HIGH': 7, 'MEDIUM': 4, 'LOW': 1}
            score += priority_weights.get(task.priority, 0)
            
            # Status weight
            if task.status == 'IN_PROGRESS':
                score += 5
            
            # Due date weight
            if task.estimated_duration:
                score += 3
            
            # Subtasks weight
            completed_subtasks = task.subtasks.filter(status='COMPLETED').count()
            total_subtasks = task.subtasks.count()
            if total_subtasks > 0:
                score += (completed_subtasks / total_subtasks) * 2
            
            prioritized.append({
                'task': task,
                'score': score
            })
        
        return sorted(prioritized, key=lambda x: x['score'], reverse=True)
    
    def assign_task_recommendations(self, task):
        """Recommend staff to assign task to"""
        from scheduling.models import AssignedShift
        from accounts.models import CustomUser
        
        recommendations = []
        
        if not task.shift:
            return recommendations
        
        # Get staff assigned to same shift
        same_shift_staff = AssignedShift.objects.filter(
            schedule=task.shift.schedule,
            shift_date=task.shift.shift_date,
            role=task.shift.role
        ).values_list('staff', flat=True)
        
        # Get their current task load
        for staff_id in same_shift_staff:
            staff = CustomUser.objects.get(id=staff_id)
            task_count = ShiftTask.objects.filter(
                assigned_to=staff,
                status__in=['TODO', 'IN_PROGRESS']
            ).count()
            
            recommendations.append({
                'staff': staff,
                'current_tasks': task_count,
                'utilization': 'Low' if task_count < 3 else 'Medium' if task_count < 6 else 'High'
            })
        
        # Sort by task count (lowest first)
        return sorted(recommendations, key=lambda x: x['current_tasks'])