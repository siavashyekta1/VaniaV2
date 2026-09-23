import logging
import sys

from django.core.management.base import BaseCommand

from capabilities.registry import CapabilityRegistry
from definitions.sync import DefinitionSync


class Command(BaseCommand):
    help = "Synchronize static definitions (Plans, Agents, Config) to the Database"

    def add_arguments(self, parser):
        parser.add_argument(
            "--agent-prompt",
            action="append",
            dest="agent_prompt_slugs",
            default=[],
            metavar="SLUG",
            help=(
                "Update the system prompt for this existing agent slug. "
                "Repeat the option to update more than one prompt."
            ),
        )

    def handle(self, *args, **options):
        logging.getLogger("django")

        self.stdout.write(self.style.WARNING("[Sync] Starting definition synchronization..."))

        try:
            DefinitionSync.sync_all(agent_prompt_slugs=options["agent_prompt_slugs"])
            CapabilityRegistry.autodiscover()
            CapabilityRegistry.sync_to_db()
            self.stdout.write(self.style.SUCCESS("[Sync] Definitions synchronized successfully."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"[Sync] Failed: {e}"))
            sys.exit(1)
