from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    # Demo has no support-contact data migration 0013; retain its own history.
    dependencies = [("billing", "0012_alter_subscriptionplan_duration_days_and_more")]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="subscription_duration_days",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="invoice",
            name="duration_extension_amount",
            field=models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00")),
        ),
    ]
