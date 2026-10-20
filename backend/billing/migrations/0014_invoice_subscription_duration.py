from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("billing", "0013_update_support_contact_details")]

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
