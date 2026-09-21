from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.events.models import Event

from .services.automation import trigger_rules_for_event


@receiver(post_save, sender=Event)
def enqueue_automation_runs(sender, instance, created, **kwargs):
    if created:
        trigger_rules_for_event(instance)
