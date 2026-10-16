"""Billing customer notifications — email + Telegram via EXISTING infra.

  * Email    -> apps.notifications.services.NotificationService
  * Telegram -> apps.bot_integration.transactional.TransactionalTelegramService

Rules:
  * NEVER raises: a notification problem must never break payment flows,
    webhook processing, or admin actions.
  * Idempotency is guaranteed by the callers' atomic claims (conditional
    DB updates / per-refund get_or_create); each helper is invoked at most
    once per claimed transition, so webhook retries cannot duplicate
    messages.
  * One event = one message per channel (a chargeback's cancellation notice
    is a single message, not a "dispute" message plus a "cancelled" message).
"""
import logging

logger = logging.getLogger(__name__)


def _amount(intent):
    return f"{intent.currency} {intent.amount_dollars:.2f}"


def _base_context(intent):
    """Shared branding + payment context for every billing email."""
    from django.conf import settings
    from django.utils import timezone as _tz
    base = (getattr(settings, "SITE_BASE_URL", "") or "").rstrip("/")
    try:
        from django.urls import reverse
        dashboard_url = base + reverse("dashboard")
    except Exception:
        dashboard_url = (base + "/") if base else "#"
    offer = None
    try:
        from apps.academy.models import CourseOffer
        offer = (CourseOffer.objects.filter(plan_id=intent.plan_id)
                 .select_related("course").first())
    except Exception:
        logger.exception("could not resolve product label for PaymentIntent %s", intent.pk)
    product_type = "course" if offer else "subscription"
    product_label = offer.course.title if offer else intent.plan.name
    learning_url = (base + f"/learn/course/{offer.course.slug}/") if offer and base else ""
    context = {
        "username": intent.user.username,
        "plan_name": product_label,
        "product_label": product_label,
        "product_type": product_type,
        "learning_url": learning_url,
        "amount_display": _amount(intent),
        "currency": intent.currency,
        "provider_display": intent.get_provider_display(),
        "provider_reference": intent.provider_reference,
        "provider_payment_id": intent.provider_payment_id,
        "date_display": _tz.localtime(intent.created_at).strftime("%b %d, %Y"),
        "dashboard_url": dashboard_url,
        "support_url": (base + "/support/") if base else "#",
    }
    logo = getattr(settings, "EMAIL_LOGO_URL", "")
    if logo:
        context["logo_url"] = logo
    if intent.applied_coupon_code:
        context["coupon_label"] = (
            f"{intent.applied_coupon_code} "
            f"(−{intent.currency} {intent.coupon_discount_cents / 100:.2f})")
    return context


def notify_payment_succeeded(payment_intent, subscription=None):
    """Receipt sent exactly once per successful payment (claim-guaranteed by
    the caller: activate_paid_subscription only returns activated=True once)."""
    context = _base_context(payment_intent)
    plan_name = context["product_label"]
    product_noun = "Course" if context["product_type"] == "course" else "Plan"
    if subscription is not None and getattr(subscription, "expires_at", None):
        from django.utils import timezone as _tz
        context["expires_display"] = _tz.localtime(
            subscription.expires_at).strftime("%b %d, %Y")
    telegram_text = (
        "\u2705 Payment received, " + payment_intent.user.username + ".\n\n"
        product_noun + ": " + plan_name + "\nAmount: " + context["amount_display"] + "\n\n"
        "Your receipt has been emailed to you. Thank you!"
    )
    _send(payment_intent.user, "payments/email/payment_success",
          "Receipt: " + plan_name + " \u2014 " + context["amount_display"],
          context, telegram_text)


def notify_payment_failed(payment_intent):
    """Customer initiated a payment but the provider never confirmed it."""
    context = _base_context(payment_intent)
    plan_name = context["product_label"]
    product_noun = "Course" if context["product_type"] == "course" else "Plan"
    context["amount"] = context["amount_display"]
    telegram_text = (
        "\u274c Payment failed, " + payment_intent.user.username + ".\n\n"
        product_noun + ": " + plan_name + "\nAmount: " + context["amount_display"] + "\n\n"
        "You have not been charged. You can retry the payment anytime from "
        "your dashboard."
    )
    _send(payment_intent.user, "payments/email/payment_failed",
          "Your payment for " + plan_name + " failed", context, telegram_text)


def notify_refunded(payment_intent):
    """A refund was recorded against the customer's payment."""
    refunded = payment_intent.refunded_cents / 100
    full = payment_intent.is_fully_refunded
    context = _base_context(payment_intent)
    plan_name = context["product_label"]
    product_noun = "course" if context["product_type"] == "course" else "subscription"
    context["amount"] = context["amount_display"]
    context["refunded_amount"] = f"{payment_intent.currency} {refunded:.2f}"
    context["fully_refunded"] = full
    if context["product_type"] == "course":
        consequence = ("Course access has been revoked because the payment was fully refunded."
                       if full else "Course access remains active after this partial refund.")
    elif full:
        consequence = ("Your subscription has been cancelled because the "
                       "payment was fully refunded.")
    else:
        consequence = "Your subscription remains active."
    telegram_text = (
        "\u21a9\ufe0f Refund issued, " + payment_intent.user.username + ".\n\n"
        ("Course: " if context["product_type"] == "course" else "Plan: ") + plan_name + "\n"
        "Refunded: " + context["refunded_amount"] + " "
        "(" + ("fully" if full else "partially") + " refunded)\n\n"
        + consequence
    )
    _send(payment_intent.user, "payments/email/payment_refunded",
          "Refund issued for your " + plan_name + " payment", context, telegram_text)


def notify_chargedback(payment_intent):
    """Confirmed chargeback: revoke the affected product access once."""
    context = _base_context(payment_intent)
    plan_name = context["product_label"]
    context["amount"] = context["amount_display"]
    if context["product_type"] == "course":
        action = "Course access has been revoked."
        subject_suffix = "course access revoked"
    else:
        action = "The subscription has been cancelled and paid access, including Telegram channel access, has been revoked."
        subject_suffix = "subscription cancelled"
    telegram_text = (
        "\u26a0\ufe0f Payment disputed — " + subject_suffix + ", "
        + payment_intent.user.username + ".\n\n"
        "Product: " + plan_name + "\nAmount: " + context["amount"] + "\n\n"
        "Your payment was charged back / disputed through your payment "
        "provider and confirmed against the payment. " + action + "\n\n"
        "If you believe this is a mistake, please contact support."
    )
    _send(payment_intent.user, "payments/email/payment_chargedback",
          "Payment disputed — " + plan_name + " " + subject_suffix,
          context, telegram_text)


def _send(user, template, subject, context, telegram_text):
    email = getattr(user, "email", None)
    if email:
        try:
            from apps.notifications.services import NotificationService
            NotificationService.send_email(
                email, template, subject, context, {"billing": True})
        except Exception:
            logger.exception("billing email failed: %s", template)
    try:
        from apps.bot_integration.transactional import TransactionalTelegramService
        TransactionalTelegramService.send_text(user, telegram_text)
    except Exception:
        logger.exception("billing telegram message failed")
