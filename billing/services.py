import stripe
from django.conf import settings
from .models import Subscription, SubscriptionPlan
from accounts.models import Restaurant

class StripeService:
    def __init__(self):
        stripe.api_key = settings.STRIPE_SECRET_KEY

    def create_customer(self, restaurant: Restaurant):
        """Create a Stripe customer for the restaurant if one doesn't exist."""
        # Check if restaurant already has a subscription/customer record
        subscription, created = Subscription.objects.get_or_create(restaurant=restaurant)
        
        if not subscription.stripe_customer_id:
            try:
                customer = stripe.Customer.create(
                    email=restaurant.owner.email,
                    name=restaurant.name,
                    metadata={
                        'restaurant_id': str(restaurant.id),
                        'restaurant_name': restaurant.name
                    }
                )
                subscription.stripe_customer_id = customer.id
                subscription.save()
                return customer
            except Exception as e:
                print(f"Error creating Stripe customer: {e}")
                raise e
        return stripe.Customer.retrieve(subscription.stripe_customer_id)

    def create_checkout_session(self, restaurant, price_id, success_url, cancel_url):
        """Create a Stripe Checkout Session for a subscription."""
        subscription = restaurant.subscription
        if not subscription.stripe_customer_id:
            self.create_customer(restaurant)
            # Refresh from db
            subscription.refresh_from_db()

        try:
            checkout_session = stripe.checkout.Session.create(
                customer=subscription.stripe_customer_id,
                payment_method_types=['card'],
                line_items=[
                    {
                        'price': price_id,
                        'quantity': 1,
                    },
                ],
                mode='subscription',
                success_url=success_url,
                cancel_url=cancel_url,
                metadata={
                    'restaurant_id': str(restaurant.id),
                    'price_id': price_id
                }
            )
            return checkout_session.url
        except Exception as e:
            print(f"Error creating checkout session: {e}")
            raise e

    def create_portal_session(self, restaurant, return_url):
        """Create a generic Customer Portal session."""
        subscription = restaurant.subscription
        if not subscription.stripe_customer_id:
            self.create_customer(restaurant)
            subscription.refresh_from_db()

        try:
            portal_session = stripe.billing_portal.Session.create(
                customer=subscription.stripe_customer_id,
                return_url=return_url,
            )
            return portal_session.url
        except Exception as e:
            print(f"Error creating portal session: {e}")
            raise e

    def get_active_subscription(self, subscription_id):
        """Retrieve subscription details from Stripe."""
        try:
            return stripe.Subscription.retrieve(subscription_id)
        except Exception as e:
            print(f"Error retrieving subscription: {e}")
            return None
