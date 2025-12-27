import stripe
from django.conf import settings
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework import viewsets, status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.decorators import action

from .models import SubscriptionPlan, Subscription
from .serializers import SubscriptionPlanSerializer, SubscriptionSerializer
from .services import StripeService

stripe.api_key = settings.STRIPE_SECRET_KEY

class PlanViewSet(viewsets.ReadOnlyModelViewSet):
    """List available subscription plans."""
    queryset = SubscriptionPlan.objects.filter(is_active=True)
    serializer_class = SubscriptionPlanSerializer
    permission_classes = [permissions.AllowAny] # Allow viewing plans without auth? Maybe IsAuthenticated.
    permission_classes = [permissions.IsAuthenticated]


class SubscriptionViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        """Get current restaurant's subscription status."""
        restaurant = request.user.restaurant
        if not restaurant:
            return Response({"error": "User does not belong to a restaurant"}, status=400)
            
        subscription, created = Subscription.objects.get_or_create(restaurant=restaurant)
        
        # If no customer ID, creating one now is good practice
        if not subscription.stripe_customer_id:
            service = StripeService()
            service.create_customer(restaurant)
            subscription.refresh_from_db()
            
        serializer = SubscriptionSerializer(subscription)
        return Response(serializer.data)

    @action(detail=False, methods=['post'])
    def checkout(self, request):
        """Create a checkout session to upgrade/subscribe."""
        restaurant = request.user.restaurant
        price_id = request.data.get('price_id')
        success_url = request.data.get('success_url')
        cancel_url = request.data.get('cancel_url')

        if not price_id or not success_url or not cancel_url:
            return Response({"error": "Missing price_id, success_url, or cancel_url"}, status=400)

        service = StripeService()
        try:
            url = service.create_checkout_session(restaurant, price_id, success_url, cancel_url)
            return Response({'url': url})
        except Exception as e:
            return Response({'error': str(e)}, status=500)

    @action(detail=False, methods=['post'])
    def portal(self, request):
        """Create a portal session to manage subscription."""
        restaurant = request.user.restaurant
        return_url = request.data.get('return_url')
        
        if not return_url:
            return Response({"error": "Missing return_url"}, status=400)

        service = StripeService()
        try:
            url = service.create_portal_session(restaurant, return_url)
            return Response({'url': url})
        except Exception as e:
            return Response({'error': str(e)}, status=500)


@method_decorator(csrf_exempt, name='dispatch')
class StripeWebhookView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        payload = request.body
        sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
        event = None

        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
            )
        except ValueError as e:
            # Invalid payload
            return Response({'error': 'Invalid payload'}, status=400)
        except stripe.error.SignatureVerificationError as e:
            # Invalid signature
            return Response({'error': 'Invalid signature'}, status=400)

        # Handle the event
        if event['type'] == 'customer.subscription.updated':
            self._handle_subscription_update(event['data']['object'])
        elif event['type'] == 'invoice.payment_succeeded':
            self._handle_payment_succeeded(event['data']['object'])
        # Add other events designed in implementation plan?

        return Response({'status': 'success'})

    def _handle_subscription_update(self, session):
        stripe_sub_id = session.get('id')
        stripe_cust_id = session.get('customer')
        status = session.get('status')
        current_period_start = timezone.datetime.fromtimestamp(session.get('current_period_start'), tz=timezone.utc)
        current_period_end = timezone.datetime.fromtimestamp(session.get('current_period_end'), tz=timezone.utc)
        cancel_at_period_end = session.get('cancel_at_period_end')
        
        # Find subscription locally
        try:
            sub = Subscription.objects.get(stripe_customer_id=stripe_cust_id)
            sub.stripe_subscription_id = stripe_sub_id
            sub.status = status
            sub.current_period_start = current_period_start
            sub.current_period_end = current_period_end
            sub.cancel_at_period_end = cancel_at_period_end
            
            # Update plan reference if changed? 
            # session['plan']['id'] corresponds to price ID or plan object
            price_id = session.get('plan', {}).get('id')
            if price_id:
                plan = SubscriptionPlan.objects.filter(stripe_price_id=price_id).first()
                if plan:
                    sub.plan = plan

            sub.save()
            print(f"Updated subscription for customer {stripe_cust_id}")

        except Subscription.DoesNotExist:
            print(f"Subscription not found for customer {stripe_cust_id}")

    def _handle_payment_succeeded(self, invoice):
        # Could log invoice, extend subscription locally if manual, but subscription.updated usually handles status
        pass
