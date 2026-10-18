import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("test_app", "0002_described"),
    ]

    operations = [
        migrations.CreateModel(
            name="Loan",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("shelf", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="loans", to="test_app.shelf")),
            ],
        ),
    ]
