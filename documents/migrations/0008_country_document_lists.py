"""Country document lists in Document Groups; University lists removed (issue 80).

Light for the micro server: two small columns, one with a default, and two short updates. Rows of
University lists are deleted (demo data only); Course and one-Student rows stay as they are.
"""

from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

# The usual Document Group of the default types that are not used when applying (migration 0002).
USUAL_GROUPS = {
    "Bank Statement": "visa",
    "Sponsor Letter": "visa",
    "Statement of Purpose": "statements",
}


def drop_university_lists(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    requirement = apps.get_model("documents", "DocumentRequirement")
    # The field is gone from today's model, so it is named as text.
    requirement.objects.filter(**{"university__isnull": False}).delete()


def set_usual_groups(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    document_type = apps.get_model("documents", "DocumentType")
    for name, group in USUAL_GROUPS.items():
        document_type.objects.filter(name__iexact=name).update(usual_group=group)


class Migration(migrations.Migration):
    dependencies = [
        ("catalogue", "0001_initial"),
        ("documents", "0007_bin"),
        ("students", "0006_chosen_offer"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="documentrequirement",
            name="documents_requirement_for_exactly_one",
        ),
        migrations.RemoveConstraint(
            model_name="documentrequirement",
            name="documents_requirement_unique_per_university",
        ),
        migrations.RunPython(drop_university_lists, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="documentrequirement",
            name="university",
        ),
        migrations.AddField(
            model_name="documentrequirement",
            name="country",
            field=models.CharField(
                blank=True,
                choices=[
                    ("australia", "Australia"),
                    ("uk", "UK"),
                    ("canada", "Canada"),
                    ("usa", "USA"),
                    ("new_zealand", "New Zealand"),
                    ("japan", "Japan"),
                    ("other", "Other"),
                ],
                default="",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="documentrequirement",
            name="group",
            field=models.CharField(
                blank=True,
                choices=[("applying", "Applying"), ("visa", "Visa"), ("statements", "Statements")],
                default="",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="documenttype",
            name="usual_group",
            field=models.CharField(
                choices=[("applying", "Applying"), ("visa", "Visa"), ("statements", "Statements")],
                default="applying",
                max_length=20,
            ),
        ),
        migrations.RunPython(set_usual_groups, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="documentrequirement",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        models.Q(("country", ""), _negated=True),
                        models.Q(("group", ""), _negated=True),
                        ("course__isnull", True),
                        ("student__isnull", True),
                    ),
                    models.Q(
                        ("country", ""),
                        ("course__isnull", False),
                        ("group", ""),
                        ("student__isnull", True),
                    ),
                    models.Q(
                        ("country", ""),
                        ("course__isnull", True),
                        ("group", ""),
                        ("student__isnull", False),
                    ),
                    _connector="OR",
                ),
                name="documents_requirement_for_exactly_one",
            ),
        ),
        migrations.AddConstraint(
            model_name="documentrequirement",
            constraint=models.UniqueConstraint(
                models.F("document_type"),
                models.F("country"),
                condition=models.Q(("country", ""), _negated=True),
                name="documents_requirement_unique_per_country",
            ),
        ),
    ]
