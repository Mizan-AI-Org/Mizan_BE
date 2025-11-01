"""
Task Assignment Service

This service handles intelligent task assignment based on:
- Role-based permissions and capabilities
- Skill matching and requirements
- Staff availability and workload
- Task priority and dependencies
- Compliance and certification requirements
"""

from django.db.models import Q, Count, Avg, Sum
from django.utils import timezone
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import logging

from .task_templates import Task, TaskTemplate
from accounts.models import CustomUser, Role, UserRole, StaffProfile
from .models import AssignedShift

logger = logging.getLogger(__name__)


class TaskAssignmentService:
    """Service for intelligent task assignment with role-based logic"""
    
    # Role-based task type mappings
    ROLE_TASK_MAPPINGS = {
        'CHEF': [
            'FOOD_PREP', 'COOKING', 'KITCHEN_CLEANING', 'INVENTORY_CHECK',
            'TEMPERATURE_CHECK', 'FOOD_SAFETY', 'RECIPE_PREPARATION'
        ],
        'KITCHEN_STAFF': [
            'FOOD_PREP', 'KITCHEN_CLEANING', 'DISHWASHING', 'INVENTORY_CHECK',
            'TEMPERATURE_CHECK', 'FOOD_SAFETY'
        ],
        'WAITER': [
            'TABLE_SETUP', 'CUSTOMER_SERVICE', 'ORDER_TAKING', 'FOOD_SERVING',
            'DINING_AREA_CLEANING', 'CASH_HANDLING'
        ],
        'CASHIER': [
            'CASH_HANDLING', 'POS_OPERATIONS', 'CUSTOMER_SERVICE', 'INVENTORY_CHECK',
            'FRONT_DESK_CLEANING'
        ],
        'MANAGER': [
            'SUPERVISION', 'QUALITY_CONTROL', 'STAFF_COORDINATION', 'INVENTORY_MANAGEMENT',
            'COMPLIANCE_CHECK', 'REPORTING', 'TRAINING'
        ],
        'SUPERVISOR': [
            'SUPERVISION', 'QUALITY_CONTROL', 'STAFF_COORDINATION', 'COMPLIANCE_CHECK',
            'TRAINING'
        ],
        'CLEANER': [
            'GENERAL_CLEANING', 'RESTROOM_CLEANING', 'DINING_AREA_CLEANING',
            'KITCHEN_CLEANING', 'WASTE_MANAGEMENT'
        ],
        'DELIVERY': [
            'DELIVERY', 'VEHICLE_CHECK', 'ORDER_PREPARATION', 'CUSTOMER_SERVICE'
        ]
    }
    
    # Priority weights for assignment scoring
    PRIORITY_WEIGHTS = {
        'URGENT': 100,
        'HIGH': 75,
        'MEDIUM': 50,
        'LOW': 25
    }
    
    def __init__(self, restaurant_id: str):
        self.restaurant_id = restaurant_id
    
    def assign_task_intelligently(self, task: Task, consider_workload: bool = True) -> Optional[CustomUser]:
        """
        Intelligently assign a task to the most suitable staff member
        
        Args:
            task: Task instance to assign
            consider_workload: Whether to consider current workload in assignment
            
        Returns:
            CustomUser instance of assigned staff member or None if no suitable match
        """
        try:
            # Get eligible staff members
            eligible_staff = self._get_eligible_staff(task)
            
            if not eligible_staff:
                logger.warning(f"No eligible staff found for task {task.id}")
                return None
            
            # Score each staff member
            staff_scores = []
            for staff in eligible_staff:
                score = self._calculate_assignment_score(task, staff, consider_workload)
                staff_scores.append((staff, score))
            
            # Sort by score (highest first)
            staff_scores.sort(key=lambda x: x[1], reverse=True)
            
            # Return the best match
            best_staff = staff_scores[0][0]
            
            logger.info(f"Task {task.id} assigned to {best_staff.email} with score {staff_scores[0][1]}")
            return best_staff
            
        except Exception as e:
            logger.error(f"Error in intelligent task assignment: {str(e)}")
            return None
    
    def assign_multiple_tasks(self, tasks: List[Task], optimize_workload: bool = True) -> Dict[str, Optional[str]]:
        """
        Assign multiple tasks optimally to distribute workload
        
        Args:
            tasks: List of Task instances to assign
            optimize_workload: Whether to optimize for balanced workload
            
        Returns:
            Dictionary mapping task IDs to assigned user IDs
        """
        assignments = {}
        
        # Sort tasks by priority and dependencies
        sorted_tasks = self._sort_tasks_for_assignment(tasks)
        
        for task in sorted_tasks:
            assigned_user = self.assign_task_intelligently(task, consider_workload=optimize_workload)
            assignments[str(task.id)] = str(assigned_user.id) if assigned_user else None
            
            # Update task assignment in database
            if assigned_user:
                task.assigned_to.add(assigned_user)
                task.save()
        
        return assignments
    
    def _get_eligible_staff(self, task: Task) -> List[CustomUser]:
        """Get staff members eligible for a specific task"""
        
        # Base query for active staff in the restaurant
        base_query = CustomUser.objects.filter(
            restaurant_id=self.restaurant_id,
            is_active=True
        ).select_related('profile')
        
        # Filter by role compatibility
        if hasattr(task.template, 'template_type') and task.template and task.template.template_type:
            compatible_roles = self._get_compatible_roles(task.template.template_type)
            if compatible_roles:
                base_query = base_query.filter(
                    restaurant_roles__role__name__in=compatible_roles,
                    restaurant_roles__restaurant_id=self.restaurant_id
                )
        
        # Filter by required skills
        if task.required_skills:
            # This would require a skills model/field - for now, we'll use role-based filtering
            pass
        
        # Filter by required certifications
        if task.required_certifications:
            # This would require a certifications model - for now, we'll use role-based filtering
            pass
        
        # Filter by availability (if task has specific timing)
        if task.due_date and task.due_time:
            available_staff = self._filter_by_availability(base_query, task.due_date, task.due_time)
            return list(available_staff)
        
        return list(base_query.distinct())
    
    def _get_compatible_roles(self, template_type: str) -> List[str]:
        """Get roles compatible with a specific template type"""
        
        role_mappings = {
            'OPENING': ['MANAGER', 'SUPERVISOR', 'CHEF', 'WAITER', 'CASHIER'],
            'CLOSING': ['MANAGER', 'SUPERVISOR', 'CHEF', 'WAITER', 'CLEANER'],
            'FOOD_PREP': ['CHEF', 'KITCHEN_STAFF'],
            'CLEANING': ['CLEANER', 'KITCHEN_STAFF', 'WAITER'],
            'INVENTORY': ['MANAGER', 'SUPERVISOR', 'CHEF', 'CASHIER'],
            'CUSTOMER_SERVICE': ['WAITER', 'CASHIER', 'MANAGER'],
            'MAINTENANCE': ['CLEANER', 'MANAGER'],
            'COMPLIANCE': ['MANAGER', 'SUPERVISOR'],
            'TRAINING': ['MANAGER', 'SUPERVISOR', 'CHEF'],
            'DELIVERY': ['DELIVERY'],
            'CUSTOM': ['MANAGER', 'SUPERVISOR', 'CHEF', 'WAITER', 'CASHIER', 'KITCHEN_STAFF', 'CLEANER']
        }
        
        return role_mappings.get(template_type, ['MANAGER', 'SUPERVISOR'])
    
    def _filter_by_availability(self, staff_query, due_date: datetime.date, due_time: datetime.time) -> List[CustomUser]:
        """Filter staff by availability at specific date/time"""
        
        # Get staff who have shifts on the due date
        available_staff = staff_query.filter(
            assigned_shifts__shift__date=due_date,
            assigned_shifts__shift__start_time__lte=due_time,
            assigned_shifts__shift__end_time__gte=due_time,
            assigned_shifts__status='CONFIRMED'
        )
        
        return list(available_staff.distinct())
    
    def _calculate_assignment_score(self, task: Task, staff: CustomUser, consider_workload: bool = True) -> float:
        """Calculate assignment score for a staff member and task"""
        
        score = 0.0
        
        # Base score from role compatibility
        role_score = self._calculate_role_compatibility_score(task, staff)
        score += role_score * 0.4  # 40% weight
        
        # Skill matching score
        skill_score = self._calculate_skill_matching_score(task, staff)
        score += skill_score * 0.3  # 30% weight
        
        # Workload balance score
        if consider_workload:
            workload_score = self._calculate_workload_score(staff)
            score += workload_score * 0.2  # 20% weight
        
        # Performance history score
        performance_score = self._calculate_performance_score(staff)
        score += performance_score * 0.1  # 10% weight
        
        return score
    
    def _calculate_role_compatibility_score(self, task: Task, staff: CustomUser) -> float:
        """Calculate how well staff role matches task requirements"""
        
        # Get staff's primary role
        try:
            user_role = UserRole.objects.filter(
                user=staff,
                restaurant_id=self.restaurant_id,
                is_primary=True
            ).first()
            
            if not user_role:
                return 0.0
            
            staff_role = user_role.role.name
            
            # Check if task template type is compatible with staff role
            if hasattr(task.template, 'template_type') and task.template and task.template.template_type:
                compatible_roles = self._get_compatible_roles(task.template.template_type)
                if staff_role in compatible_roles:
                    # Higher score for exact matches
                    if staff_role in ['MANAGER', 'SUPERVISOR']:
                        return 1.0  # Managers/supervisors can handle most tasks
                    else:
                        return 0.8  # Role-specific staff get good score for their tasks
                else:
                    return 0.2  # Low score for incompatible roles
            
            return 0.5  # Default score when no specific template type
            
        except Exception as e:
            logger.error(f"Error calculating role compatibility: {str(e)}")
            return 0.0
    
    def _calculate_skill_matching_score(self, task: Task, staff: CustomUser) -> float:
        """Calculate skill matching score"""
        
        # For now, return a base score based on role
        # In a full implementation, this would check actual skill records
        try:
            user_role = UserRole.objects.filter(
                user=staff,
                restaurant_id=self.restaurant_id,
                is_primary=True
            ).first()
            
            if not user_role:
                return 0.5
            
            # Higher skill scores for specialized roles
            skill_multipliers = {
                'CHEF': 0.9,
                'MANAGER': 0.95,
                'SUPERVISOR': 0.85,
                'KITCHEN_STAFF': 0.7,
                'WAITER': 0.75,
                'CASHIER': 0.7,
                'CLEANER': 0.6,
                'DELIVERY': 0.65
            }
            
            return skill_multipliers.get(user_role.role.name, 0.5)
            
        except Exception as e:
            logger.error(f"Error calculating skill matching: {str(e)}")
            return 0.5
    
    def _calculate_workload_score(self, staff: CustomUser) -> float:
        """Calculate workload balance score (higher score = less current workload)"""
        
        try:
            # Count current active tasks
            current_tasks = Task.objects.filter(
                assigned_to=staff,
                status__in=['TODO', 'IN_PROGRESS'],
                restaurant_id=self.restaurant_id
            ).count()
            
            # Calculate score inversely proportional to workload
            if current_tasks == 0:
                return 1.0
            elif current_tasks <= 3:
                return 0.8
            elif current_tasks <= 6:
                return 0.6
            elif current_tasks <= 10:
                return 0.4
            else:
                return 0.2
                
        except Exception as e:
            logger.error(f"Error calculating workload score: {str(e)}")
            return 0.5
    
    def _calculate_performance_score(self, staff: CustomUser) -> float:
        """Calculate performance score based on task completion history"""
        
        try:
            # Get completed tasks in the last 30 days
            thirty_days_ago = timezone.now() - timedelta(days=30)
            
            completed_tasks = Task.objects.filter(
                completed_by=staff,
                completed_at__gte=thirty_days_ago,
                restaurant_id=self.restaurant_id
            )
            
            if not completed_tasks.exists():
                return 0.7  # Default score for new staff
            
            # Calculate completion rate and average time
            total_tasks = completed_tasks.count()
            
            # Simple performance metric based on completion count
            if total_tasks >= 20:
                return 1.0
            elif total_tasks >= 15:
                return 0.9
            elif total_tasks >= 10:
                return 0.8
            elif total_tasks >= 5:
                return 0.7
            else:
                return 0.6
                
        except Exception as e:
            logger.error(f"Error calculating performance score: {str(e)}")
            return 0.7
    
    def _sort_tasks_for_assignment(self, tasks: List[Task]) -> List[Task]:
        """Sort tasks for optimal assignment order"""
        
        def task_priority_key(task):
            # Priority score
            priority_score = self.PRIORITY_WEIGHTS.get(task.priority, 50)
            
            # Urgency score based on due date
            urgency_score = 0
            if task.due_date:
                days_until_due = (task.due_date - timezone.now().date()).days
                if days_until_due <= 0:
                    urgency_score = 100  # Overdue
                elif days_until_due == 1:
                    urgency_score = 80   # Due tomorrow
                elif days_until_due <= 3:
                    urgency_score = 60   # Due within 3 days
                else:
                    urgency_score = 20   # Future tasks
            
            # Critical task bonus
            critical_bonus = 50 if task.is_critical else 0
            
            return priority_score + urgency_score + critical_bonus
        
        return sorted(tasks, key=task_priority_key, reverse=True)
    
    def get_assignment_recommendations(self, task: Task, limit: int = 5) -> List[Dict]:
        """Get ranked list of assignment recommendations for a task"""
        
        eligible_staff = self._get_eligible_staff(task)
        recommendations = []
        
        for staff in eligible_staff:
            score = self._calculate_assignment_score(task, staff)
            
            # Get additional context
            current_workload = Task.objects.filter(
                assigned_to=staff,
                status__in=['TODO', 'IN_PROGRESS'],
                restaurant_id=self.restaurant_id
            ).count()
            
            user_role = UserRole.objects.filter(
                user=staff,
                restaurant_id=self.restaurant_id,
                is_primary=True
            ).first()
            
            recommendations.append({
                'user_id': str(staff.id),
                'name': f"{staff.first_name} {staff.last_name}",
                'email': staff.email,
                'role': user_role.role.name if user_role else 'Unknown',
                'score': round(score, 2),
                'current_workload': current_workload,
                'recommendation_reason': self._get_recommendation_reason(task, staff, score)
            })
        
        # Sort by score and limit results
        recommendations.sort(key=lambda x: x['score'], reverse=True)
        return recommendations[:limit]
    
    def _get_recommendation_reason(self, task: Task, staff: CustomUser, score: float) -> str:
        """Generate human-readable recommendation reason"""
        
        try:
            user_role = UserRole.objects.filter(
                user=staff,
                restaurant_id=self.restaurant_id,
                is_primary=True
            ).first()
            
            role_name = user_role.role.name if user_role else 'Unknown'
            
            if score >= 0.8:
                return f"Excellent match - {role_name} with relevant experience and low workload"
            elif score >= 0.6:
                return f"Good match - {role_name} with suitable skills"
            elif score >= 0.4:
                return f"Adequate match - {role_name} available but may need guidance"
            else:
                return f"Limited match - {role_name} available but not ideal for this task"
                
        except Exception as e:
            return "Available staff member"
    
    def validate_assignment(self, task: Task, staff: CustomUser) -> Tuple[bool, str]:
        """Validate if a staff member can be assigned to a task"""
        
        # Check if staff is active
        if not staff.is_active:
            return False, "Staff member is not active"
        
        # Check if staff belongs to the same restaurant
        if staff.restaurant_id != self.restaurant_id:
            return False, "Staff member belongs to different restaurant"
        
        # Check role compatibility
        if hasattr(task.template, 'template_type') and task.template and task.template.template_type:
            compatible_roles = self._get_compatible_roles(task.template.template_type)
            user_role = UserRole.objects.filter(
                user=staff,
                restaurant_id=self.restaurant_id,
                is_primary=True
            ).first()
            
            if user_role and user_role.role.name not in compatible_roles:
                return False, f"Staff role ({user_role.role.name}) not compatible with task type"
        
        # Check availability if task has specific timing
        if task.due_date and task.due_time:
            available_staff = self._filter_by_availability(
                CustomUser.objects.filter(id=staff.id),
                task.due_date,
                task.due_time
            )
            if not available_staff:
                return False, "Staff member not available at task due time"
        
        # Check required certifications
        if task.required_certifications:
            # This would check actual certification records
            # For now, we'll assume managers/supervisors have all certifications
            user_role = UserRole.objects.filter(
                user=staff,
                restaurant_id=self.restaurant_id,
                is_primary=True
            ).first()
            
            if user_role and user_role.role.name not in ['MANAGER', 'SUPERVISOR']:
                return False, "Staff member may not have required certifications"
        
        return True, "Assignment is valid"
    
    def get_workload_analysis(self) -> Dict:
        """Get workload analysis for all staff members"""
        
        staff_workloads = []
        
        staff_members = CustomUser.objects.filter(
            restaurant_id=self.restaurant_id,
            is_active=True
        ).select_related('profile')
        
        for staff in staff_members:
            # Current active tasks
            active_tasks = Task.objects.filter(
                assigned_to=staff,
                status__in=['TODO', 'IN_PROGRESS'],
                restaurant_id=self.restaurant_id
            )
            
            # Completed tasks this week
            week_start = timezone.now().date() - timedelta(days=timezone.now().weekday())
            completed_this_week = Task.objects.filter(
                completed_by=staff,
                completed_at__date__gte=week_start,
                restaurant_id=self.restaurant_id
            ).count()
            
            # Get role
            user_role = UserRole.objects.filter(
                user=staff,
                restaurant_id=self.restaurant_id,
                is_primary=True
            ).first()
            
            staff_workloads.append({
                'user_id': str(staff.id),
                'name': f"{staff.first_name} {staff.last_name}",
                'role': user_role.role.name if user_role else 'Unknown',
                'active_tasks': active_tasks.count(),
                'completed_this_week': completed_this_week,
                'workload_level': self._get_workload_level(active_tasks.count())
            })
        
        return {
            'staff_workloads': staff_workloads,
            'total_active_tasks': sum(s['active_tasks'] for s in staff_workloads),
            'average_workload': sum(s['active_tasks'] for s in staff_workloads) / len(staff_workloads) if staff_workloads else 0
        }
    
    def _get_workload_level(self, task_count: int) -> str:
        """Get workload level description"""
        if task_count == 0:
            return 'Available'
        elif task_count <= 3:
            return 'Light'
        elif task_count <= 6:
            return 'Moderate'
        elif task_count <= 10:
            return 'Heavy'
        else:
            return 'Overloaded'