import re

from django.db import migrations


PERSON_LABEL = re.compile(r"\bبیمار(ان(?:ی)?)?\b")


def standardize_visitor_terminology(apps, schema_editor):
    """Update configured display text while preserving identifiers and custom prompts."""
    database = schema_editor.connection.alias
    configured_text = (
        ("services", "AgentService", ("name", "description", "system_prompt", "user_guide")),
        ("services", "CanvasType", ("name", "description")),
        ("billing", "FAQ", ("question", "answer", "category")),
        ("users", "UserRole", ("name",)),
    )
    for app_label, model_name, fields in configured_text:
        model = apps.get_model(app_label, model_name)
        rows = model.objects.using(database).values("pk", *fields).iterator()
        for row in rows:
            updates = {}
            for field in fields:
                original = row[field]
                if not isinstance(original, str):
                    continue
                renamed = PERSON_LABEL.sub(
                    lambda match: "مراجع" + (match.group(1) or ""), original
                ).replace("مراجع یا مراجع", "مراجع")
                if renamed != original:
                    updates[field] = renamed
            if updates:
                model.objects.using(database).filter(pk=row["pk"]).update(**updates)


class Migration(migrations.Migration):
    dependencies = [
        ("services", "0008_agentservice_audience_and_more"),
        ("billing", "0006_faq_billingconfig_support_address_and_more"),
        ("users", "0005_role_slug_expert_visitor"),
    ]

    operations = [
        migrations.RunPython(standardize_visitor_terminology, migrations.RunPython.noop),
    ]

