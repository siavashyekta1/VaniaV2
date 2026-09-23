from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0007_customuser_national_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="customuser",
            name="has_all_expert_access",
            field=models.BooleanField(
                default=False,
                help_text="Grant access to every active expert assistant regardless of profession or plan inclusion.",
            ),
        ),
    ]
