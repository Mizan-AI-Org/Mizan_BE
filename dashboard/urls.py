from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views_ops_ui import (
    ManagerValidateTaskView,
    ManagerValidateOrderView,
    ManagerRequireValidationView,
    DashboardOpsSearchView,
    ManagerChaseRecordView,
    StaffDailyTaskProgressView,
    StaffDailyProgressHistoryView,
)
from .views import (
    DailyKPIListAPIView,
    StaffCapturedOrderListCreateAPIView,
    StaffCapturedOrderRetrieveUpdateDestroyAPIView,
    AlertListCreateAPIView,
    AlertRetrieveUpdateDestroyAPIView,
    TaskListCreateAPIView,
    TaskRetrieveUpdateDestroyAPIView,
)
from .views_extended import (
    TaskManagementViewSet,
    TaskCategoryViewSet,
    DashboardAnalyticsViewSet,
    AlertViewSet
)
from .api.summary import DashboardSummaryView
from .api.action_center import ActionCenterView
from .api.portfolio import PortfolioSummaryView, LocationDetailView
from .api.tasks_demands import (
    TasksDemandsView,
    TaskDemandDetailView,
    TaskStatusUpdateView,
    TaskBucketUpdateView,
    TaskAssigneeUpdateView,
)
from .api.operations_live import (
    OperationsLiveView,
    agent_list_operations_live,
    agent_notify_manager_urgent,
)
from .api.custom_widget_tasks import CustomWidgetTasksView
from .api.staff_messages import (
    StaffMessagesRecentView,
    StaffMessagesSendView,
)
from .api.meetings_reminders import MeetingsRemindersView
from .api.my_tasks import MyTasksView
from .api.personal_reminders_ui import PersonalReminderDetailView, PersonalRemindersUIView
from .api.tenant_documents_ui import TenantDocumentsListView
from .api.clock_ins import DashboardClockInsView
from .api.category_tasks import CategoryTasksView
from .views import mark_shift_no_show
from .views_widget_layout import (
    AgentDashboardCategoryCreateView,
    AgentDashboardCustomWidgetDeleteView,
    AgentDashboardWidgetCreateView,
    AgentDashboardWidgetListView,
    AgentDashboardWidgetsAddView,
    AgentDashboardWidgetsRemoveView,
    AgentDashboardWidgetsReorderView,
    AgentTenantBootstrapView,
    DashboardCustomWidgetListView,
    DashboardWidgetOrderView,
)
from .views_categories import (
    DashboardCategoryDetailView,
    DashboardCategoryListCreateView,
    DashboardCustomWidgetCreateView,
    DashboardCustomWidgetDetailView,
)
from .views_agent import (
    agent_create_dashboard_task,
    agent_reassign_dashboard_task,
    agent_update_dashboard_task_status,
    agent_update_dashboard_task,
    agent_list_dashboard_tasks,
)
from .views_ops_memory import (
    agent_validate_task,
    agent_submit_task_proof,
    agent_department_owners,
    agent_search_tasks_and_staff,
    agent_classify_checkin_message,
    agent_detect_order_station,
)
from .api.cross_location_report import agent_cross_location_report
from .api.calendar_write import agent_create_calendar_event
from .api.photo_router import agent_parse_photo
from .api.document_router import agent_parse_document

router = DefaultRouter()
router.register(r'tasks', TaskManagementViewSet, basename='task-management')
router.register(r'task-categories', TaskCategoryViewSet, basename='task-category')
router.register(r'analytics', DashboardAnalyticsViewSet, basename='analytics')
router.register(r'alerts', AlertViewSet, basename='alert')

urlpatterns = [
    path('', include(router.urls)),
    path('widget-order/', DashboardWidgetOrderView.as_view(), name='dashboard-widget-order'),
    path('custom-widgets/', DashboardCustomWidgetListView.as_view(), name='dashboard-custom-widgets-list'),
    path('custom-widgets/create/', DashboardCustomWidgetCreateView.as_view(), name='dashboard-custom-widgets-create'),
    path(
        'custom-widgets/<uuid:pk>/tasks/',
        CustomWidgetTasksView.as_view(),
        name='dashboard-custom-widget-tasks',
    ),
    path(
        'custom-widgets/<uuid:pk>/',
        DashboardCustomWidgetDetailView.as_view(),
        name='dashboard-custom-widgets-detail',
    ),
    path(
        'categories/',
        DashboardCategoryListCreateView.as_view(),
        name='dashboard-categories',
    ),
    path(
        'categories/<uuid:pk>/',
        DashboardCategoryDetailView.as_view(),
        name='dashboard-categories-detail',
    ),
    path('agent/tasks/create/', agent_create_dashboard_task, name='dashboard-agent-tasks-create'),
    path('agent/tasks/reassign/', agent_reassign_dashboard_task, name='dashboard-agent-tasks-reassign'),
    path('agent/tasks/status/', agent_update_dashboard_task_status, name='dashboard-agent-tasks-status'),
    path('agent/tasks/update/', agent_update_dashboard_task, name='dashboard-agent-tasks-update'),
    path('agent/tasks/list/', agent_list_dashboard_tasks, name='dashboard-agent-tasks-list'),
    path(
        'agent/operations-live/',
        agent_list_operations_live,
        name='dashboard-agent-operations-live',
    ),
    path(
        'agent/operations-live/notify/',
        agent_notify_manager_urgent,
        name='dashboard-agent-operations-live-notify',
    ),
    path('agent/tasks/validate/', agent_validate_task, name='dashboard-agent-tasks-validate'),
    path('agent/tasks/proof/', agent_submit_task_proof, name='dashboard-agent-tasks-proof'),
    path('agent/department-owners/', agent_department_owners, name='dashboard-agent-department-owners'),
    path('agent/search/', agent_search_tasks_and_staff, name='dashboard-agent-search'),
    path('agent/checkin-message/', agent_classify_checkin_message, name='dashboard-agent-checkin-message'),
    path('agent/order-station/', agent_detect_order_station, name='dashboard-agent-order-station'),
    path(
        'agent/cross-location-report/',
        agent_cross_location_report,
        name='dashboard-agent-cross-location-report',
    ),
    path(
        'agent/calendar-events/create/',
        agent_create_calendar_event,
        name='dashboard-agent-calendar-events-create',
    ),
    path(
        'agent/parse-photo/',
        agent_parse_photo,
        name='dashboard-agent-parse-photo',
    ),
    path(
        'agent/parse-document/',
        agent_parse_document,
        name='dashboard-agent-parse-document',
    ),
    path(
        'agent/widgets/resolve-tenant/',
        AgentTenantBootstrapView.as_view(),
        name='dashboard-agent-widgets-resolve-tenant',
    ),
    path('agent/widgets/list/', AgentDashboardWidgetListView.as_view(), name='dashboard-agent-widgets-list'),
    path('agent/widgets/add/', AgentDashboardWidgetsAddView.as_view(), name='dashboard-agent-widgets-add'),
    path('agent/widgets/remove/', AgentDashboardWidgetsRemoveView.as_view(), name='dashboard-agent-widgets-remove'),
    path('agent/widgets/reorder/', AgentDashboardWidgetsReorderView.as_view(), name='dashboard-agent-widgets-reorder'),
    path('agent/widgets/create/', AgentDashboardWidgetCreateView.as_view(), name='dashboard-agent-widgets-create'),
    path('agent/widgets/custom/delete/', AgentDashboardCustomWidgetDeleteView.as_view(), name='dashboard-agent-widgets-custom-delete'),
    path('agent/categories/create/', AgentDashboardCategoryCreateView.as_view(), name='dashboard-agent-categories-create'),
    path('summary/', DashboardSummaryView.as_view(), name='dashboard-summary'),
    path('portfolio/', PortfolioSummaryView.as_view(), name='dashboard-portfolio'),
    path(
        'portfolio/locations/<uuid:loc_id>/',
        LocationDetailView.as_view(),
        name='dashboard-portfolio-location-detail',
    ),
    path('action-center/', ActionCenterView.as_view(), name='dashboard-action-center'),
    path(
        'tasks-demands/',
        TasksDemandsView.as_view(),
        name='dashboard-tasks-demands',
    ),
    path(
        'operations-live/',
        OperationsLiveView.as_view(),
        name='dashboard-operations-live',
    ),
    path(
        'tasks-demands/<uuid:pk>/',
        TaskDemandDetailView.as_view(),
        name='dashboard-tasks-demands-detail',
    ),
    path(
        'tasks-demands/<uuid:pk>/status/',
        TaskStatusUpdateView.as_view(),
        name='dashboard-tasks-demands-status',
    ),
    # Drag-and-drop "move this row to another widget" endpoint. The
    # FE calls it whenever a card is dropped on a different category
    # widget, and the backend dispatches by source model.
    path(
        'tasks-demands/<uuid:pk>/bucket/',
        TaskBucketUpdateView.as_view(),
        name='dashboard-tasks-demands-bucket',
    ),
    # Reassign endpoint used by the row dropdown's "Reassign" entry.
    # Same dispatcher pattern as bucket / status — one URL across
    # StaffRequest / dashboard.Task / scheduling.Task / Invoice.
    path(
        'tasks-demands/<uuid:pk>/assignee/',
        TaskAssigneeUpdateView.as_view(),
        name='dashboard-tasks-demands-assignee',
    ),
    # Admin → Staff WhatsApp messaging surface for the dashboard.
    # The recent feed powers the delivery / read receipts widget;
    # the send endpoint is the structured composer alternative to
    # talking to Miya in the chat panel.
    path(
        'staff-messages/recent/',
        StaffMessagesRecentView.as_view(),
        name='dashboard-staff-messages-recent',
    ),
    path(
        'staff-messages/send/',
        StaffMessagesSendView.as_view(),
        name='dashboard-staff-messages-send',
    ),
    path(
        'meetings-reminders/',
        MeetingsRemindersView.as_view(),
        name='dashboard-meetings-reminders',
    ),
    path('my-tasks/', MyTasksView.as_view(), name='dashboard-my-tasks'),
    path(
        'personal-reminders/',
        PersonalRemindersUIView.as_view(),
        name='dashboard-personal-reminders',
    ),
    path(
        'personal-reminders/<uuid:pk>/',
        PersonalReminderDetailView.as_view(),
        name='dashboard-personal-reminder-detail',
    ),
    path(
        'tenant-documents/',
        TenantDocumentsListView.as_view(),
        name='dashboard-tenant-documents',
    ),
    path(
        'clock-ins/',
        DashboardClockInsView.as_view(),
        name='dashboard-clock-ins',
    ),
    path(
        'category-tasks/',
        CategoryTasksView.as_view(),
        name='dashboard-category-tasks',
    ),
    path('attendance/mark-no-show/', mark_shift_no_show, name='dashboard-mark-no-show'),
    path('kpis/', DailyKPIListAPIView.as_view(), name='daily-kpi-list'),
    path('captured-orders/', StaffCapturedOrderListCreateAPIView.as_view(), name='staff-captured-orders'),
    path(
        'captured-orders/<uuid:pk>/',
        StaffCapturedOrderRetrieveUpdateDestroyAPIView.as_view(),
        name='staff-captured-order-detail',
    ),
    path(
        'captured-orders/<uuid:pk>/validate/',
        ManagerValidateOrderView.as_view(),
        name='staff-captured-order-validate',
    ),
    path(
        'tasks/<uuid:pk>/validate/',
        ManagerValidateTaskView.as_view(),
        name='dashboard-task-validate',
    ),
    path(
        'tasks/<uuid:pk>/require-validation/',
        ManagerRequireValidationView.as_view(),
        name='dashboard-task-require-validation',
    ),
    path(
        'ops-search/',
        DashboardOpsSearchView.as_view(),
        name='dashboard-ops-search',
    ),
    path(
        'records/chase/',
        ManagerChaseRecordView.as_view(),
        name='dashboard-records-chase',
    ),
    path(
        'staff-daily-progress/',
        StaffDailyTaskProgressView.as_view(),
        name='dashboard-staff-daily-progress',
    ),
    path(
        'staff-daily-progress/history/',
        StaffDailyProgressHistoryView.as_view(),
        name='dashboard-staff-daily-progress-history',
    ),
    path('alerts-old/', AlertListCreateAPIView.as_view(), name='alert-list-create'),
    path('alerts-old/<uuid:pk>/', AlertRetrieveUpdateDestroyAPIView.as_view(), name='alert-detail'),
    path('tasks-old/', TaskListCreateAPIView.as_view(), name='task-list-create'),
    path('tasks-old/<uuid:pk>/', TaskRetrieveUpdateDestroyAPIView.as_view(), name='task-detail'),
]
