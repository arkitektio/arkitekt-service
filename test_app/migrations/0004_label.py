import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("test_app", "0003_loan"),
    ]

    operations = [
        migrations.CreateModel(
            name="Label",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("shelf", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="labels", to="test_app.shelf")),
            ],
        ),
    ]
