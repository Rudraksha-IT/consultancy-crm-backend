from __future__ import annotations

from django.urls import URLPattern, path

from document_examples.views import (
    BinExampleLinkView,
    BinExampleRestoreView,
    BinExampleView,
    ExampleLinkView,
    ExampleListView,
    ExampleView,
    InstructionView,
    ReplaceExampleView,
)

ONE_TYPE = "configuration/document-types/<document_type:type_id>/"
ONE_EXAMPLE = "configuration/document-examples/<example:example_id>/"
ONE_BINNED = "documents/bin/document-examples/<example:example_id>/"

urlpatterns: list[URLPattern] = [
    path(f"{ONE_TYPE}instruction/", InstructionView.as_view(), name="document-type-instruction"),
    path(f"{ONE_TYPE}examples/", ExampleListView.as_view(), name="document-type-examples"),
    path(ONE_EXAMPLE, ExampleView.as_view(), name="document-example"),
    path(f"{ONE_EXAMPLE}replace/", ReplaceExampleView.as_view(), name="document-example-replace"),
    path(
        "documents/examples/<example:example_id>/link/",
        ExampleLinkView.as_view(),
        name="document-example-link",
    ),
    path(ONE_BINNED, BinExampleView.as_view(), name="bin-document-example"),
    path(
        f"{ONE_BINNED}restore/",
        BinExampleRestoreView.as_view(),
        name="bin-document-example-restore",
    ),
    path(f"{ONE_BINNED}link/", BinExampleLinkView.as_view(), name="bin-document-example-link"),
]
