from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .services import AIAssistantService, TaskManagementAI


class AIAssistantViewSet(viewsets.ViewSet):
    """
    AI Assistant API
    - Insights and recommendations
    - Task management suggestions
    - Cost optimization tips
    - Scheduling recommendations
    """
    permission_classes = [IsAuthenticated]
    
    @action(detail=False, methods=['get'])
    def insights(self, request):
        """Get AI-powered insights for the restaurant"""
        if not request.user.restaurant:
            return Response(
                {'error': 'No restaurant associated'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        service = AIAssistantService(restaurant=request.user.restaurant)
        insights = service.generate_insights()
        
        return Response({
            'insights': insights,
            'count': len(insights),
            'generated_at': str(timezone.now())
        })
    
    @action(detail=False, methods=['get'])
    def dashboard_summary(self, request):
        """Get AI dashboard summary with all recommendations"""
        if not request.user.restaurant:
            return Response(
                {'error': 'No restaurant associated'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        service = AIAssistantService(restaurant=request.user.restaurant)
        
        return Response({
            'context': service.get_restaurant_context(),
            'insights': service.generate_insights(),
            'scheduling_recommendations': service.get_scheduling_recommendations(),
            'cost_optimization_tips': service.get_cost_optimization_tips(),
            'task_suggestions': service.generate_task_suggestions()
        })
    
    @action(detail=False, methods=['get'])
    def task_suggestions(self, request):
        """Get AI suggestions for daily tasks"""
        if not request.user.restaurant:
            return Response(
                {'error': 'No restaurant associated'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        service = AIAssistantService(restaurant=request.user.restaurant)
        suggestions = service.generate_task_suggestions()
        
        return Response({
            'suggestions': suggestions,
            'count': len(suggestions)
        })
    
    @action(detail=False, methods=['get'])
    def scheduling_recommendations(self, request):
        """Get AI recommendations for staff scheduling"""
        if not request.user.restaurant:
            return Response(
                {'error': 'No restaurant associated'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        service = AIAssistantService(restaurant=request.user.restaurant)
        recommendations = service.get_scheduling_recommendations()
        
        return Response({
            'recommendations': recommendations,
            'count': len(recommendations)
        })
    
    @action(detail=False, methods=['get'])
    def cost_optimization(self, request):
        """Get AI tips for cost optimization"""
        if not request.user.restaurant:
            return Response(
                {'error': 'No restaurant associated'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        service = AIAssistantService(restaurant=request.user.restaurant)
        tips = service.get_cost_optimization_tips()
        
        return Response({
            'tips': tips,
            'count': len(tips)
        })
    
    @action(detail=False, methods=['post'])
    def ask_question(self, request):
        """Ask AI assistant a question"""
        if not request.user.restaurant:
            return Response(
                {'error': 'No restaurant associated'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        question = request.data.get('question', '')
        context_type = request.data.get('context', 'general')
        
        if not question:
            return Response(
                {'error': 'Question is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        service = AIAssistantService(restaurant=request.user.restaurant)
        
        # Build context based on type
        context = {}
        if context_type in ['general', 'operations']:
            context = service.get_restaurant_context()
        
        # Generate answer based on question
        answer = self._generate_answer(question, context)
        
        return Response({
            'question': question,
            'answer': answer,
            'context_type': context_type,
            'confidence': 'HIGH'
        })
    
    def _generate_answer(self, question, context):
        """Generate AI answer based on question and context"""
        question_lower = question.lower()
        
        # Revenue questions
        if any(word in question_lower for word in ['revenue', 'sales', 'earnings', 'profit']):
            return (
                f"Based on your restaurant data, your average daily revenue is "
                f"${context.get('recent_revenue', 0):.2f}. Consider analyzing trends "
                f"to identify peak hours and adjust staffing accordingly."
            )
        
        # Staffing questions
        elif any(word in question_lower for word in ['staff', 'schedule', 'employee', 'shift']):
            return (
                f"You currently have {context.get('today_staff_count', 0)} staff scheduled for today. "
                f"With {context.get('avg_daily_orders', 0):.0f} average daily orders, "
                f"aim for 1 staff member per 10-15 orders for optimal efficiency."
            )
        
        # Task questions
        elif any(word in question_lower for word in ['task', 'work', 'todo']):
            return (
                f"You have {context.get('pending_tasks', 0)} pending tasks. "
                f"Consider prioritizing urgent items first and delegating work based on staff expertise."
            )
        
        # Cost questions
        elif any(word in question_lower for word in ['cost', 'expense', 'budget', 'reduce']):
            return (
                "To reduce costs, focus on: 1) Labor optimization through better scheduling, "
                "2) Inventory waste reduction through better forecasting, "
                "3) Energy efficiency during off-peak hours. "
                "View our cost optimization tips for specific recommendations."
            )
        
        # Default answer
        else:
            return (
                "I'm analyzing your restaurant data. Based on current metrics, here are my insights: "
                f"Average daily revenue is ${context.get('recent_revenue', 0):.2f}, "
                f"with {context.get('avg_daily_orders', 0):.0f} orders on average. "
                f"You have {context.get('pending_tasks', 0)} pending tasks and "
                f"{context.get('today_staff_count', 0)} staff scheduled today. "
                "Would you like specific recommendations for any area?"
            )


class TaskAssignmentAIViewSet(viewsets.ViewSet):
    """AI-powered task assignment recommendations"""
    permission_classes = [IsAuthenticated]
    
    @action(detail=False, methods=['post'])
    def recommend_assignment(self, request):
        """Get AI recommendation for task assignment"""
        from scheduling.models import ShiftTask
        
        task_id = request.data.get('task_id')
        
        if not task_id:
            return Response(
                {'error': 'task_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            task = ShiftTask.objects.get(id=task_id)
        except ShiftTask.DoesNotExist:
            return Response(
                {'error': 'Task not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        if not request.user.restaurant:
            return Response(
                {'error': 'No restaurant associated'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        ai = TaskManagementAI(request.user.restaurant)
        recommendations = ai.assign_task_recommendations(task)
        
        return Response({
            'task_id': task_id,
            'task_title': task.title,
            'recommendations': [
                {
                    'staff_id': rec['staff'].id,
                    'staff_name': f"{rec['staff'].first_name} {rec['staff'].last_name}",
                    'email': rec['staff'].email,
                    'current_tasks': rec['current_tasks'],
                    'utilization': rec['utilization'],
                    'score': 100 - (rec['current_tasks'] * 10)
                }
                for rec in recommendations
            ]
        })
    
    @action(detail=False, methods=['post'])
    def prioritize_tasks(self, request):
        """Get AI prioritization of tasks"""
        from scheduling.models import ShiftTask
        
        if not request.user.restaurant:
            return Response(
                {'error': 'No restaurant associated'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get all pending tasks for the restaurant
        tasks = ShiftTask.objects.filter(
            shift__schedule__restaurant=request.user.restaurant,
            status__in=['TODO', 'IN_PROGRESS']
        )[:20]  # Limit to 20 tasks
        
        ai = TaskManagementAI(request.user.restaurant)
        prioritized = ai.prioritize_tasks(tasks)
        
        return Response({
            'prioritized_tasks': [
                {
                    'task_id': item['task'].id,
                    'title': item['task'].title,
                    'priority': item['task'].priority,
                    'status': item['task'].status,
                    'assigned_to': f"{item['task'].assigned_to.first_name} {item['task'].assigned_to.last_name}" if item['task'].assigned_to else "Unassigned",
                    'ai_score': item['score'],
                    'recommendation_rank': i + 1
                }
                for i, item in enumerate(prioritized)
            ],
            'total_tasks': len(prioritized)
        })


from django.utils import timezone