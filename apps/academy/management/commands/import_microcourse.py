"""Validate and stage an authored TradeThesis micro-course for administrator review."""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.academy.importing import inspect_manifest, stage_course_import


class Command(BaseCommand):
    help = "Validate a micro-course manifest and import it as a review-only draft."

    def add_arguments(self, parser):
        parser.add_argument("--manifest", required=True, help="Path to course-manifest.json")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Validate the manifest and source files without writing to the database or media storage.",
        )

    def handle(self, *args, **options):
        manifest = Path(options["manifest"]).expanduser().resolve()
        try:
            payload, asset_sources, warnings = inspect_manifest(manifest)
        except (OSError, ValueError, KeyError) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            f"Validated {payload['course']['title']}: "
            f"{sum(len(module['lessons']) for module in payload['modules'])} lessons, "
            f"{len(payload['assets'])} referenced image assets."
        )
        for warning in warnings:
            self.stdout.write(self.style.WARNING("Asset review: " + warning))

        if options["dry_run"]:
            self.stdout.write(self.style.SUCCESS("Dry run complete; no records or files were written."))
            return

        try:
            record, created, stage_warnings = stage_course_import(
                manifest_path=manifest,
                payload=payload,
                asset_sources=asset_sources,
                warnings=warnings,
                imported_by=None,
            )
        except Exception as exc:
            raise CommandError(f"Course import failed without publishing learner content: {exc}") from exc

        for warning in stage_warnings:
            self.stdout.write(self.style.WARNING("Asset review: " + warning))
        action = "Staged new review draft" if created else "Reused existing import (idempotent)"
        self.stdout.write(self.style.SUCCESS(f"{action}: {record.title} [{record.pk}]"))
        self.stdout.write("Open Django Admin → Academy → Course imports → Review to inspect and publish.")
