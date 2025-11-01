from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from django_filters import FilterSet
from django.db.models import Q, Count, Sum, Avg
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from datetime import datetime, timedelta
import json
import csv
import io

from .models import ScheduleTemplate, TemplateVersion, TemplateShift, ShiftTask
from .serializers import (
    ScheduleTemplateSerializer, 
    TemplateVersionSerializer,
    ScheduleTemplateDetailSerializer
)
from .audit import AuditTrailService


class TemplateFilter(FilterSet):
    """Advanced filtering for schedule templates"""
    
    class Meta:
        model = ScheduleTemplate
        fields = {
            'restaurant': ['exact'],
            'is_active': ['exact'],
            'name': ['exact', 'icontains']
        }


class ScheduleTemplateViewSet(viewsets.ModelViewSet):
    """Enhanced viewset for schedule template management with version control"""
    
    queryset = ScheduleTemplate.objects.all()
    serializer_class = ScheduleTemplateSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = TemplateFilter
    search_fields = ['name', 'description', 'tags']
    ordering_fields = ['name', 'created_at', 'updated_at', 'usage_count']
    ordering = ['-updated_at']
    
    def get_serializer_class(self):
        if self.action in ['retrieve', 'create', 'update', 'partial_update']:
            return ScheduleTemplateDetailSerializer
        return ScheduleTemplateSerializer
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Add annotations for statistics
        queryset = queryset.annotate(
            shifts_count=Count('shifts', distinct=True),
            tasks_count=Count('tasks', distinct=True),
            versions_count=Count('versions', distinct=True)
        )
        
        # Filter by favorites if requested
        if self.request.query_params.get('favorites') == 'true':
            queryset = queryset.filter(is_favorite=True)
            
        # Filter by usage
        min_usage = self.request.query_params.get('min_usage')
        if min_usage:
            queryset = queryset.filter(usage_count__gte=int(min_usage))
            
        return queryset
    
    def perform_create(self, serializer):
        template = serializer.save(created_by=self.request.user)
        
        # Create initial version
        TemplateVersion.objects.create(
            template=template,
            version='1.0',
            created_by=self.request.user,
            changes_summary='Initial template creation',
            is_current=True
        )
        
        # Log audit trail
        AuditTrailService.log_template_activity(
            user=self.request.user,
            template=template,
            action='create',
            details={'name': template.name, 'category': template.category}
        )
    
    def perform_update(self, serializer):
        old_template = self.get_object()
        template = serializer.save()
        
        # Create new version if significant changes
        if self._has_significant_changes(old_template, template):
            # Mark current version as not current
            TemplateVersion.objects.filter(
                template=template, 
                is_current=True
            ).update(is_current=False)
            
            # Create new version
            version_number = self._get_next_version_number(template)
            TemplateVersion.objects.create(
                template=template,
                version=version_number,
                created_by=self.request.user,
                changes_summary=self._generate_changes_summary(old_template, template),
                is_current=True
            )
        
        # Log audit trail
        AuditTrailService.log_template_activity(
            user=self.request.user,
            template=template,
            action='update',
            details={'changes': self._get_changed_fields(old_template, template)}
        )
    
    def perform_destroy(self, instance):
        # Log audit trail before deletion
        AuditTrailService.log_template_activity(
            user=self.request.user,
            template=instance,
            action='delete',
            details={'name': instance.name}
        )
        super().perform_destroy(instance)
    
    @action(detail=True, methods=['post'])
    def duplicate(self, request, pk=None):
        """Create a duplicate of the template"""
        original_template = self.get_object()
        
        with transaction.atomic():
            # Create duplicate template
            duplicate_template = ScheduleTemplate.objects.create(
                name=f"{original_template.name} (Copy)",
                description=original_template.description,
                category=original_template.category,
                tags=original_template.tags,
                estimated_labor_hours=original_template.estimated_labor_hours,
                estimated_cost=original_template.estimated_cost,
                created_by=request.user,
                is_active=False  # Start as inactive
            )
            
            # Duplicate shifts
            for shift in original_template.shifts.all():
                Shift.objects.create(
                    template=duplicate_template,
                    role=shift.role,
                    start_time=shift.start_time,
                    end_time=shift.end_time,
                    day_of_week=shift.day_of_week,
                    break_duration=shift.break_duration,
                    hourly_rate=shift.hourly_rate
                )
            
            # Duplicate tasks
            for task in original_template.tasks.all():
                Task.objects.create(
                    template=duplicate_template,
                    title=task.title,
                    description=task.description,
                    category=task.category,
                    estimated_duration=task.estimated_duration,
                    priority=task.priority,
                    required_role=task.required_role,
                    day_of_week=task.day_of_week,
                    start_time=task.start_time
                )
            
            # Create initial version
            TemplateVersion.objects.create(
                template=duplicate_template,
                version='1.0',
                created_by=request.user,
                changes_summary=f'Duplicated from "{original_template.name}"',
                is_current=True
            )
            
            # Log audit trail
            AuditTrailService.log_template_activity(
                user=request.user,
                template=duplicate_template,
                action='duplicate',
                details={
                    'original_template': original_template.name,
                    'original_id': original_template.id
                }
            )
        
        serializer = self.get_serializer(duplicate_template)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['post'])
    def toggle_favorite(self, request, pk=None):
        """Toggle favorite status of template"""
        template = self.get_object()
        template.is_favorite = not template.is_favorite
        template.save()
        
        # Log audit trail
        AuditTrailService.log_template_activity(
            user=request.user,
            template=template,
            action='toggle_favorite',
            details={'is_favorite': template.is_favorite}
        )
        
        return Response({
            'is_favorite': template.is_favorite,
            'message': f'Template {"added to" if template.is_favorite else "removed from"} favorites'
        })
    
    @action(detail=True, methods=['get'])
    def versions(self, request, pk=None):
        """Get version history for template"""
        template = self.get_object()
        versions = template.versions.all().order_by('-created_at')
        serializer = TemplateVersionSerializer(versions, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def restore_version(self, request, pk=None):
        """Restore a specific version of the template"""
        template = self.get_object()
        version_id = request.data.get('version_id')
        
        if not version_id:
            return Response(
                {'error': 'version_id is required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            version_to_restore = template.versions.get(id=version_id)
        except TemplateVersion.DoesNotExist:
            return Response(
                {'error': 'Version not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        with transaction.atomic():
            # Mark current version as not current
            template.versions.filter(is_current=True).update(is_current=False)
            
            # Create new version based on restored version
            new_version_number = self._get_next_version_number(template)
            TemplateVersion.objects.create(
                template=template,
                version=new_version_number,
                created_by=request.user,
                changes_summary=f'Restored from version {version_to_restore.version}',
                is_current=True
            )
            
            # Log audit trail
            AuditTrailService.log_template_activity(
                user=request.user,
                template=template,
                action='restore_version',
                details={
                    'restored_version': version_to_restore.version,
                    'new_version': new_version_number
                }
            )
        
        return Response({
            'message': f'Template restored to version {version_to_restore.version}',
            'new_version': new_version_number
        })
    
    @action(detail=False, methods=['post'])
    def bulk_actions(self, request):
        """Perform bulk actions on multiple templates"""
        template_ids = request.data.get('template_ids', [])
        action_type = request.data.get('action')
        
        if not template_ids or not action_type:
            return Response(
                {'error': 'template_ids and action are required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        templates = ScheduleTemplate.objects.filter(id__in=template_ids)
        
        if not templates.exists():
            return Response(
                {'error': 'No templates found'}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        results = {'success': 0, 'failed': 0, 'errors': []}
        
        with transaction.atomic():
            for template in templates:
                try:
                    if action_type == 'activate':
                        template.is_active = True
                        template.save()
                    elif action_type == 'deactivate':
                        template.is_active = False
                        template.save()
                    elif action_type == 'delete':
                        template.delete()
                    elif action_type == 'add_to_favorites':
                        template.is_favorite = True
                        template.save()
                    elif action_type == 'remove_from_favorites':
                        template.is_favorite = False
                        template.save()
                    else:
                        results['errors'].append(f'Unknown action: {action_type}')
                        results['failed'] += 1
                        continue
                    
                    # Log audit trail
                    AuditTrailService.log_template_activity(
                        user=request.user,
                        template=template,
                        action=f'bulk_{action_type}',
                        details={'bulk_operation': True}
                    )
                    
                    results['success'] += 1
                    
                except Exception as e:
                    results['errors'].append(f'Template {template.id}: {str(e)}')
                    results['failed'] += 1
        
        return Response(results)
    
    @action(detail=False, methods=['get'])
    def export(self, request):
        """Export templates to CSV"""
        templates = self.filter_queryset(self.get_queryset())
        
        # Create CSV response
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="schedule_templates.csv"'
        
        writer = csv.writer(response)
        writer.writerow([
            'ID', 'Name', 'Description', 'Category', 'Is Active', 'Is Favorite',
            'Created At', 'Created By', 'Shifts Count', 'Tasks Count', 'Usage Count',
            'Estimated Labor Hours', 'Estimated Cost', 'Tags'
        ])
        
        for template in templates:
            writer.writerow([
                template.id,
                template.name,
                template.description,
                template.category,
                template.is_active,
                template.is_favorite,
                template.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                f"{template.created_by.first_name} {template.created_by.last_name}",
                getattr(template, 'shifts_count', 0),
                getattr(template, 'tasks_count', 0),
                template.usage_count,
                template.estimated_labor_hours,
                template.estimated_cost,
                ', '.join(template.tags) if template.tags else ''
            ])
        
        return response
    
    @action(detail=False, methods=['post'])
    def import_templates(self, request):
        """Import templates from CSV file"""
        if 'file' not in request.FILES:
            return Response(
                {'error': 'No file provided'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        csv_file = request.FILES['file']
        
        if not csv_file.name.endswith('.csv'):
            return Response(
                {'error': 'File must be a CSV'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            # Read CSV file
            file_data = csv_file.read().decode('utf-8')
            csv_reader = csv.DictReader(io.StringIO(file_data))
            
            results = {'success': 0, 'failed': 0, 'errors': []}
            
            with transaction.atomic():
                for row in csv_reader:
                    try:
                        # Parse tags
                        tags = [tag.strip() for tag in row.get('Tags', '').split(',') if tag.strip()]
                        
                        # Create template
                        template = ScheduleTemplate.objects.create(
                            name=row['Name'],
                            description=row.get('Description', ''),
                            category=row.get('Category', 'General'),
                            tags=tags,
                            estimated_labor_hours=float(row.get('Estimated Labor Hours', 0)),
                            estimated_cost=float(row.get('Estimated Cost', 0)),
                            created_by=request.user,
                            is_active=row.get('Is Active', 'True').lower() == 'true'
                        )
                        
                        # Create initial version
                        TemplateVersion.objects.create(
                            template=template,
                            version='1.0',
                            created_by=request.user,
                            changes_summary='Imported from CSV',
                            is_current=True
                        )
                        
                        # Log audit trail
                        AuditTrailService.log_template_activity(
                            user=request.user,
                            template=template,
                            action='import',
                            details={'source': 'csv_import'}
                        )
                        
                        results['success'] += 1
                        
                    except Exception as e:
                        results['errors'].append(f'Row {csv_reader.line_num}: {str(e)}')
                        results['failed'] += 1
            
            return Response(results)
            
        except Exception as e:
            return Response(
                {'error': f'Failed to process CSV file: {str(e)}'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=False, methods=['get'])
    def statistics(self, request):
        """Get template usage statistics"""
        templates = self.get_queryset()
        
        stats = {
            'total_templates': templates.count(),
            'active_templates': templates.filter(is_active=True).count(),
            'favorite_templates': templates.filter(is_favorite=True).count(),
            'categories': list(templates.values_list('category', flat=True).distinct()),
            'total_usage': templates.aggregate(Sum('usage_count'))['usage_count__sum'] or 0,
            'avg_labor_hours': templates.aggregate(Avg('estimated_labor_hours'))['estimated_labor_hours__avg'] or 0,
            'avg_cost': templates.aggregate(Avg('estimated_cost'))['estimated_cost__avg'] or 0,
            'most_used': templates.order_by('-usage_count').first(),
            'recently_created': templates.order_by('-created_at')[:5],
            'recently_updated': templates.order_by('-updated_at')[:5]
        }
        
        # Serialize most used template
        if stats['most_used']:
            stats['most_used'] = ScheduleTemplateSerializer(stats['most_used']).data
        
        # Serialize recent templates
        stats['recently_created'] = ScheduleTemplateSerializer(stats['recently_created'], many=True).data
        stats['recently_updated'] = ScheduleTemplateSerializer(stats['recently_updated'], many=True).data
        
        return Response(stats)
    
    def _has_significant_changes(self, old_template, new_template):
        """Check if template has significant changes that warrant a new version"""
        significant_fields = ['name', 'description', 'category', 'estimated_labor_hours', 'estimated_cost']
        
        for field in significant_fields:
            if getattr(old_template, field) != getattr(new_template, field):
                return True
        
        # Check if shifts or tasks have changed (simplified check)
        if old_template.shifts.count() != new_template.shifts.count():
            return True
        if old_template.tasks.count() != new_template.tasks.count():
            return True
            
        return False
    
    def _get_next_version_number(self, template):
        """Generate next version number"""
        latest_version = template.versions.order_by('-created_at').first()
        if not latest_version:
            return '1.0'
        
        try:
            major, minor = map(int, latest_version.version.split('.'))
            return f"{major}.{minor + 1}"
        except (ValueError, AttributeError):
            return '1.0'
    
    def _generate_changes_summary(self, old_template, new_template):
        """Generate a summary of changes between template versions"""
        changes = []
        
        if old_template.name != new_template.name:
            changes.append(f"Name changed from '{old_template.name}' to '{new_template.name}'")
        
        if old_template.description != new_template.description:
            changes.append("Description updated")
        
        if old_template.category != new_template.category:
            changes.append(f"Category changed to '{new_template.category}'")
        
        if old_template.estimated_labor_hours != new_template.estimated_labor_hours:
            changes.append("Labor hours estimate updated")
        
        if old_template.estimated_cost != new_template.estimated_cost:
            changes.append("Cost estimate updated")
        
        return '; '.join(changes) if changes else 'Template updated'
    
    def _get_changed_fields(self, old_template, new_template):
        """Get dictionary of changed fields"""
        changes = {}
        fields_to_check = ['name', 'description', 'category', 'estimated_labor_hours', 'estimated_cost', 'tags']
        
        for field in fields_to_check:
            old_value = getattr(old_template, field)
            new_value = getattr(new_template, field)
            if old_value != new_value:
                changes[field] = {'old': old_value, 'new': new_value}
        
        return changes


class TemplateVersionViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet for template version management"""
    
    queryset = TemplateVersion.objects.all()
    serializer_class = TemplateVersionSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['template', 'is_current']
    ordering = ['-created_at']
    
    @action(detail=True, methods=['get'])
    def compare(self, request, pk=None):
        """Compare this version with another version"""
        version1 = self.get_object()
        version2_id = request.query_params.get('compare_with')
        
        if not version2_id:
            return Response(
                {'error': 'compare_with parameter is required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            version2 = TemplateVersion.objects.get(id=version2_id)
        except TemplateVersion.DoesNotExist:
            return Response(
                {'error': 'Comparison version not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        if version1.template != version2.template:
            return Response(
                {'error': 'Versions must belong to the same template'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Compare versions (simplified comparison)
        comparison = {
            'version1': TemplateVersionSerializer(version1).data,
            'version2': TemplateVersionSerializer(version2).data,
            'differences': {
                'shifts_count': version1.shifts_count - version2.shifts_count,
                'tasks_count': version1.tasks_count - version2.tasks_count,
                'time_difference': (version1.created_at - version2.created_at).days
            }
        }
        
        return Response(comparison)