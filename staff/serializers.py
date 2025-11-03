from rest_framework import serializers
from .models import Schedule, StaffProfile, ScheduleChange, ScheduleNotification, StaffAvailability, PerformanceMetric
from .models_task import StandardOperatingProcedure, SafetyChecklist, ScheduleTask, SafetyConcernReport, SafetyRecognition
from accounts.serializers import CustomUserSerializer, RestaurantSerializer
import decimal
from accounts.models import CustomUser, Restaurant

class StaffProfileSerializer(serializers.ModelSerializer):
    user_details = CustomUserSerializer(source='user', read_only=True)
    
    class Meta:
        model = StaffProfile
        fields = '__all__'
        read_only_fields = ('user',)

class ScheduleSerializer(serializers.ModelSerializer):
    staff_details = CustomUserSerializer(source='staff', read_only=True)
    restaurant_details = RestaurantSerializer(source='restaurant', read_only=True)
    
    class Meta:
        model = Schedule
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at', 'backup_data')
        
    def validate(self, data):
        """
        Validate schedule data to ensure start_time is before end_time
        and recurring schedules have a pattern
        """
        if 'start_time' in data and 'end_time' in data:
            if data['start_time'] >= data['end_time']:
                raise serializers.ValidationError("End time must be after start time")
                
        if data.get('is_recurring', False) and not data.get('recurrence_pattern'):
            raise serializers.ValidationError("Recurrence pattern is required for recurring schedules")
            
        return data
        
    def create(self, validated_data):
        # Set the created_by field to the current user
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            validated_data['created_by'] = request.user
            validated_data['last_modified_by'] = request.user
            
        return super().create(validated_data)
        
    def update(self, instance, validated_data):
        # Set the last_modified_by field to the current user
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            validated_data['last_modified_by'] = request.user
            
        return super().update(instance, validated_data)

class ScheduleChangeSerializer(serializers.ModelSerializer):
    changed_by_details = CustomUserSerializer(source='changed_by', read_only=True)
    
    class Meta:
        model = ScheduleChange
        fields = '__all__'
        read_only_fields = ('id', 'timestamp')

class ScheduleNotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduleNotification
        fields = '__all__'
        read_only_fields = ('id', 'created_at')

class StaffAvailabilitySerializer(serializers.ModelSerializer):
    day_name = serializers.SerializerMethodField()
    
    class Meta:
        model = StaffAvailability
        fields = '__all__'
        
    def get_day_name(self, obj):
        days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        return days[obj.day_of_week]

class PerformanceMetricSerializer(serializers.ModelSerializer):
    staff_details = CustomUserSerializer(source='staff', read_only=True)
    
    class Meta:
        model = PerformanceMetric
        fields = '__all__'

# New serializers for task management models
class StandardOperatingProcedureSerializer(serializers.ModelSerializer):
    class Meta:
        model = StandardOperatingProcedure
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at')

class SafetyChecklistSerializer(serializers.ModelSerializer):
    class Meta:
        model = SafetyChecklist
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at')

class ScheduleTaskSerializer(serializers.ModelSerializer):
    sop_details = StandardOperatingProcedureSerializer(source='sop', read_only=True)
    checklist_details = SafetyChecklistSerializer(source='safety_checklist', read_only=True)
    assigned_to_details = CustomUserSerializer(source='assigned_to', read_only=True)
    
    class Meta:
        model = ScheduleTask
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at', 'completion_time')

class SafetyConcernReportSerializer(serializers.ModelSerializer):
    reporter_details = CustomUserSerializer(source='reporter', read_only=True, required=False)
    
    class Meta:
        model = SafetyConcernReport
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at')
        
    def create(self, validated_data):
        # Handle anonymous reports
        request = self.context.get('request')
        if not validated_data.get('is_anonymous') and request and hasattr(request, 'user'):
            validated_data['reporter'] = request.user
        elif validated_data.get('is_anonymous'):
            validated_data['reporter'] = None
            
        return super().create(validated_data)

class SafetyRecognitionSerializer(serializers.ModelSerializer):
    staff_details = CustomUserSerializer(source='staff', read_only=True)
    recognized_by_details = CustomUserSerializer(source='recognized_by', read_only=True)
    
    class Meta:
        model = SafetyRecognition
        fields = '__all__'
        read_only_fields = ('created_at',)
        
    def create(self, validated_data):
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            validated_data['recognized_by'] = request.user
            
        return super().create(validated_data)

class TableSerializer(serializers.ModelSerializer):
    current_order_info = OrderSerializer(source='current_order', read_only=True)

    class Meta:
        model = Table
        fields = '__all__'
        read_only_fields = ('current_order_info', 'created_at', 'updated_at')

class OrderCreateSerializer(serializers.ModelSerializer):
    items = serializers.ListField(
        child=serializers.DictField(
            child=serializers.CharField() # Or more specific validation if needed
        ),
        write_only=True
    )

    class Meta:
        model = Order
        fields = ('order_type', 'table_number', 'customer_name', 'customer_phone', 'items')

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        restaurant = self.context['request'].user.restaurant
        staff = self.context['request'].user

        # Calculate subtotal, tax, and total based on items
        subtotal = 0
        order_items_for_creation = []

        for item_data in items_data:
            product = Product.objects.get(id=item_data['product_id'], restaurant=restaurant)
            quantity = int(item_data['quantity'])
            unit_price = product.base_price
            total_price = unit_price * quantity
            subtotal += total_price
            order_items_for_creation.append({
                'product': product,
                'quantity': quantity,
                'unit_price': unit_price,
                'total_price': total_price,
            })

        tax_rate = 0.1 # Example tax rate
        tax_amount = subtotal * decimal.Decimal(str(tax_rate))
        total_amount = subtotal + tax_amount

        order = Order.objects.create(
            restaurant=restaurant,
            staff=staff,
            subtotal=subtotal,
            tax_amount=tax_amount,
            total_amount=total_amount,
            **validated_data
        )

        for item_data in order_items_for_creation:
            OrderItem.objects.create(order=order, **item_data)

        return order

class StaffSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ('id', 'username', 'email', 'first_name', 'last_name')

class ScheduleSerializer(serializers.ModelSerializer):
    staff = StaffSerializer(read_only=True)
    staff_id = serializers.PrimaryKeyRelatedField(queryset=CustomUser.objects.all(), source='staff', write_only=True)

    class Meta:
        model = Schedule
        fields = ('id', 'staff', 'staff_id', 'title', 'start_time', 'end_time', 'tasks', 'is_recurring', 'recurrence_pattern')
