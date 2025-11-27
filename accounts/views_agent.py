from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from .models import CustomUser
from .serializers import CustomUserSerializer, RestaurantSerializer

class AgentContextView(APIView):
    """
    View for the AI Agent to validate a user's token and retrieve their context.
    Requires a valid Bearer token in the Authorization header.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        
        # Ensure user has a restaurant
        if not user.restaurant:
             return Response(
                {'error': 'User is not associated with any restaurant.'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Serialize data
        user_data = CustomUserSerializer(user).data
        restaurant_data = RestaurantSerializer(user.restaurant).data
        
        return Response({
            'user': {
                'id': user_data['id'],
                'email': user_data['email'],
                'first_name': user_data['first_name'],
                'last_name': user_data['last_name'],
                'role': user_data['role'],
            },
            'restaurant': {
                'id': restaurant_data['id'],
                'name': restaurant_data['name'],
                'currency': restaurant_data['currency'],
                'timezone': restaurant_data['timezone'],
                # Add other necessary fields for the agent here
            }
        })
