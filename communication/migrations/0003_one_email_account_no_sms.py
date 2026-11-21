# Ticket 04 (ADR 0023): no SMS and no office email accounts; every email has a subject line.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("communication", "0002_platform_admin_actor"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="subject",
            field=models.CharField(blank=True, default="", max_length=150),
        ),
        migrations.AlterField(
            model_name="message",
            name="channel",
            field=models.CharField(
                choices=[("sms", "SMS (no longer available)"), ("email", "Email")], max_length=10
            ),
        ),
        migrations.DeleteModel(
            name="ProviderSettings",
        ),
    ]
