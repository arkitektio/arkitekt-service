"""Name each collaboration by lok's key, one across the hub.

Rows made before carry a fresh key: no guest's token names it, so whoever was granted them
is granted again through lok, by the collaboration's new key.
"""

import uuid

from django.db import migrations, models


def give_each_a_key(apps: object, schema_editor: object) -> None:
    collaboration = apps.get_model("arkitekt_collaborations", "Collaboration")  # type: ignore[attr-defined]
    for row in collaboration.objects.filter(key__isnull=True):
        row.key = uuid.uuid4()
        row.save(update_fields=["key"])


class Migration(migrations.Migration):
    dependencies = [("arkitekt_collaborations", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="collaboration",
            name="key",
            field=models.UUIDField(null=True),
        ),
        migrations.RunPython(give_each_a_key, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="collaboration",
            name="key",
            field=models.UUIDField(
                default=uuid.uuid4,
                help_text="The collaboration's key across the hub: lok's, and what a guest's token names",
                unique=True,
            ),
        ),
        migrations.AlterField(
            model_name="collaboration",
            name="name",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
    ]
