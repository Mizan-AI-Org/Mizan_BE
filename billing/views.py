import logging

import stripe
from django.conf import settings
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Subscription, SubscriptionPlan
from .serializers import SubscriptionPlanSerializer, SubscriptionSerializer
from .services import StripeService

logger = logging.getLogger(__name__)
stripe.api_key = getattr(settings, "STRIPE_SECRET_KEY", "") or ""


class PlanViewSet(viewsets.ReadOnlyModelViewSet):
    """Public list of subscription plans (used by the pricing UI)."""

    queryset = SubscriptionPlan.objects.filter(is_active=True)
    serializer_class = SubscriptionPlanSerializer
    # Pricing must be visible pre-auth so marketing pages can render it.
    permission_classes = [permissions.AllowAny]


class SubscriptionViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        """Return the current restaurant's subscription + tier info."""
        restaurant = request.user.restaurant
        if not restaurant:
            return Response({"error": "User does not belong to a restaurant"}, status=400)

        subscription, _ = Subscription.objects.get_or_create(restaurant=restaurant)

        # Lazily create a Stripe customer so the portal button always works.
        if not subscription.stripe_customer_id and stripe.api_key:
            try:
                StripeService().create_customer(restaurant)
                subscription.refresh_from_db()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not create Stripe customer for %s: %s", restaurant.id, exc)

        return Response(SubscriptionSerializer(subscription).data)

    @action(detail=False, methods=["get"], url_path="entitlements")
    def entitlements(self, request):
        """Features + limits the current tenant is entitled to.

        Shape kept flat and stable so frontend feature-gating code can be
        simple: ``entitlements.features.includes("auto_po")``.
        """
        restaurant = request.user.restaurant
        if not restaurant:
            return Response({"error": "User does not belong to a restaurant"}, status=400)

        subscription, _ = Subscription.objects.get_or_create(restaurant=restaurant)
        plan = subscription.plan if subscription.is_paid else None

        # Fall back to the published plan for the effective tier — so pilot
        # users (no paid sub) still get accurate feature lists.
        if plan is None:
            plan = SubscriptionPlan.objects.filter(
                tier=subscription.effective_tier, is_active=True
            ).first()

        return Response({
            "tier": subscription.effective_tier,
            "status": subscription.status,
            "is_paid": subscription.is_paid,
            "billing_interval": subscription.billing_interval,
            "trial_ends_at": subscription.trial_ends_at,
            "current_period_end": subscription.current_period_end,
            "cancel_at_period_end": subscription.cancel_at_period_end,
            "features": plan.feature_keys if plan else [],
            "limits": {
                "max_locations": plan.max_locations if plan else None,
                "max_staff": plan.max_staff if plan else None,
            },
            "plan": SubscriptionPlanSerializer(plan).data if plan else None,
        })

    @action(detail=False, methods=["post"])
    def checkout(self, request):
        """Create a Stripe checkout session for the selected price."""
        restaurant = request.user.restaurant
        if not restaurant:
            return Response({"error": "User does not belong to a restaurant"}, status=400)

        price_id = request.data.get("price_id")
        success_url = request.data.get("success_url")
        cancel_url = request.data.get("cancel_url")

        if not price_id or not success_url or not cancel_url:
            return Response(
                {"error": "Missing price_id, success_url, or cancel_url"},
                status=400,
            )

        if not stripe.api_key:
            return Response(
                {"error": "Billing is not configured on this environment."},
                status=503,
            )

        # Guard against bad price IDs by ensuring the price maps to a known plan.
        known = SubscriptionPlan.objects.filter(
            is_active=True,
        ).filter(
            stripe_price_id_monthly=price_id,
        ).exists() or SubscriptionPlan.objects.filter(
            is_active=True,
        ).filter(
            stripe_price_id_yearly=price_id,
        ).exists() or SubscriptionPlan.objects.filter(
            is_active=True, stripe_price_id=price_id,
        ).exists()
        if not known:
            return Response({"error": "Unknown price_id."}, status=400)

        service = StripeService()
        try:
            url = service.create_checkout_session(restaurant, price_id, success_url, cancel_url)
            return Response({"url": url})
        except Exception as exc:  # noqa: BLE001
            logger.exception("Stripe checkout failed")
            return Response({"error": str(exc)}, status=500)

    @action(detail=False, methods=["post"])
    def portal(self, request):
        restaurant = request.user.restaurant
        if not restaurant:
            return Response({"error": "User does not belong to a restaurant"}, status=400)

        return_url = request.data.get("return_url")
        if not return_url:
            return Response({"error": "Missing return_url"}, status=400)

        if not stripe.api_key:
            return Response(
                {"error": "Billing is not configured on this environment."},
                status=503,
            )

        service = StripeService()
        try:
            url = service.create_portal_session(restaurant, return_url)
            return Response({"url": url})
        except Exception as exc:  # noqa: BLE001
            logger.exception("Stripe portal failed")
            return Response({"error": str(exc)}, status=500)


@method_decorator(csrf_exempt, name="dispatch")
class StripeWebhookView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        payload = request.body
        sig_header = request.META.get("HTTP_STRIPE_SIGNATURE")
        webhook_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "") or ""
        if not webhook_secret:
            logger.error("Stripe webhook received but STRIPE_WEBHOOK_SECRET is not configured")
            return Response({"error": "Webhook not configured"}, status=503)

        try:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        except ValueError:
            return Response({"error": "Invalid payload"}, status=400)
        except stripe.error.SignatureVerificationError:
            return Response({"error": "Invalid signature"}, status=400)

        handler = {
            "checkout.session.completed": self._handle_checkout_completed,
            "customer.subscription.created": self._handle_subscription_update,
            "customer.subscription.updated": self._handle_subscription_update,
            "customer.subscription.deleted": self._handle_subscription_deleted,
            "invoice.payment_failed": self._handle_payment_failed,
        }.get(event["type"])

        if handler:
            try:
                handler(event["data"]["object"])
            except Exception:  # noqa: BLE001
                logger.exception("Failed handling Stripe event %s", event["type"])
                return Response({"error": "Handler failed"}, status=500)

        return Response({"status": "success"})

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------
    def _subscription_for_customer(self, customer_id):
        return Subscription.objects.filter(stripe_customer_id=customer_id).first()

    def _apply_stripe_subscription(self, sub_record: Subscription, stripe_sub) -> None:
        sub_record.stripe_subscription_id = stripe_sub.get("id") or sub_record.stripe_subscription_id
        sub_record.status = stripe_sub.get("status") or sub_record.status
        sub_record.cancel_at_period_end = bool(stripe_sub.get("cancel_at_period_end"))

        for attr, key in (
            ("current_period_start", "current_period_start"),
            ("current_period_end", "current_period_end"),
            ("trial_ends_at", "trial_end"),
        ):
            value = stripe_sub.get(key)
            if value:
                setattr(sub_record, attr, timezone.datetime.fromtimestamp(value, tz=timezone.utc))

        # Price & interval — either on top-level "items.data[0].price" (new API)
        # or legacy "plan".
        price = None
        try:
            price = stripe_sub["items"]["data"][0]["price"]
        except (KeyError, IndexError, TypeError):
            price = stripe_sub.get("plan") or None
        if price:
            sub_record.billing_interval = price.get("recurring", {}).get("interval") \
                or price.get("interval") or sub_record.billing_interval
            price_id = price.get("id")
            if price_id:
                plan = SubscriptionPlan.objects.filter(
                    stripe_price_id_monthly=price_id,
                ).first() or SubscriptionPlan.objects.filter(
                    stripe_price_id_yearly=price_id,
                ).first() or SubscriptionPlan.objects.filter(
                    stripe_price_id=price_id,
                ).first()
                if plan:
                    sub_record.plan = plan

        sub_record.save()

    def _handle_checkout_completed(self, session):
        customer_id = session.get("customer")
        sub_record = self._subscription_for_customer(customer_id)
        if not sub_record:
            logger.warning("checkout.session.completed for unknown customer %s", customer_id)
            return
        stripe_sub_id = session.get("subscription")
        if stripe_sub_id:
            stripe_sub = stripe.Subscription.retrieve(stripe_sub_id)
            self._apply_stripe_subscription(sub_record, stripe_sub)

    def _handle_subscription_update(self, stripe_sub):
        sub_record = self._subscription_for_customer(stripe_sub.get("customer"))
        if not sub_record:
            logger.warning("subscription event for unknown customer %s", stripe_sub.get("customer"))
            return
        self._apply_stripe_subscription(sub_record, stripe_sub)

    def _handle_subscription_deleted(self, stripe_sub):
        sub_record = self._subscription_for_customer(stripe_sub.get("customer"))
        if not sub_record:
            return
        sub_record.status = "canceled"
        sub_record.cancel_at_period_end = False
        sub_record.save(update_fields=["status", "cancel_at_period_end", "updated_at"])

    def _handle_payment_failed(self, invoice):
        customer_id = invoice.get("customer")
        sub_record = self._subscription_for_customer(customer_id)
        if not sub_record:
            return
        sub_record.status = "past_due"
        sub_record.save(update_fields=["status", "updated_at"])
