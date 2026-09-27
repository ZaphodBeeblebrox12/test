"""Internal risk detection. Signals inform decisions; they are never
submitted to networks as evidence."""
import logging

from django.db.models import Count

logger = logging.getLogger(__name__)


def detect_shared_credentials():
    """Same card fingerprint or device fingerprint across >1 user."""
    from apps.evidence.models import CheckoutEvidence, PaymentAuthentication
    from .models import RiskSignal

    for row in PaymentAuthentication.objects.exclude(
            card_fingerprint="").values("card_fingerprint").annotate(
            n=Count("payment_intent__user", distinct=True)).filter(n__gte=2):
        RiskSignal.objects.get_or_create(
            signal_type=RiskSignal.SignalType.MULTI_ACCOUNT_CARD,
            subject_hash=row["card_fingerprint"],
            defaults={"severity": 4, "detail": {"accounts": row["n"]}})

    for row in CheckoutEvidence.objects.values(
            "device_fingerprint").annotate(
            n=Count("payment_intent__user", distinct=True)).filter(n__gte=2):
        RiskSignal.objects.get_or_create(
            signal_type=RiskSignal.SignalType.MULTI_ACCOUNT_DEVICE,
            subject_hash=row["device_fingerprint"],
            defaults={"severity": 3, "detail": {"accounts": row["n"]}})


def detect_trial_abuse():
    """INTERNAL signal only - never network-facing evidence.

    Multi-account trials: >=2 UserTrialUsage rows across accounts sharing a
    device fingerprint (checkout evidence) or a payment credential (auth).
    Repeat trial refunds: >=2 context=trial refunds for one user."""
    from apps.accounts.models import User
    from apps.evidence.models import CheckoutEvidence, PaymentAuthentication
    from apps.subscriptions.models import UserTrialUsage
    from .models import RiskSignal

    # trials sharing a device fingerprint
    for row in (CheckoutEvidence.objects
                .filter(pricing_snapshot__is_trial=True)
                .exclude(device_fingerprint="")
                .values("device_fingerprint")
                .annotate(n=Count("payment_intent__user", distinct=True))
                .filter(n__gte=2)):
        RiskSignal.objects.get_or_create(
            signal_type=RiskSignal.SignalType.TRIAL_ABUSE,
            subject_hash=row["device_fingerprint"],
            defaults={"severity": 4,
                      "detail": {"basis": "shared_device",
                                 "accounts": row["n"]}})

    # trials sharing a payment credential
    for row in (PaymentAuthentication.objects
                .filter(payment_intent__checkout_evidence__pricing_snapshot__is_trial=True)
                .exclude(card_fingerprint="")
                .values("card_fingerprint")
                .annotate(n=Count("payment_intent__user", distinct=True))
                .filter(n__gte=2)):
        RiskSignal.objects.get_or_create(
            signal_type=RiskSignal.SignalType.TRIAL_ABUSE,
            subject_hash=row["card_fingerprint"],
            defaults={"severity": 4,
                      "detail": {"basis": "shared_card",
                                 "accounts": row["n"]}})

    # repeated trial refunds per user
    from apps.payments.models import Refund
    for row in (Refund.objects
                .filter(commercial_context=Refund.CommercialContext.TRIAL)
                .values("payment_intent__user")
                .annotate(n=Count("id")).filter(n__gte=2)):
        RiskSignal.objects.get_or_create(
            signal_type=RiskSignal.SignalType.TRIAL_ABUSE,
            user_id=row["payment_intent__user"],
            defaults={"severity": 3,
                      "detail": {"basis": "repeat_trial_refunds",
                                 "count": row["n"]}})


def flag_prior_dispute(dispute):
    from .models import RiskSignal
    user = dispute.payment_intent.user
    RiskSignal.objects.get_or_create(
        signal_type=RiskSignal.SignalType.PRIOR_DISPUTE, user=user,
        defaults={"severity": 4,
                  "detail": {"dispute": str(dispute.pk),
                             "status": dispute.status}})
