"""Names assembled from fields (SPEC 7.1a), and the model asked for fields, not a name."""

from datetime import date

import pytest

from app.services import ai, naming
from app.services.errors import ValidationError
from app.services.naming import NameFields, assemble_name, clean_fields

MAY_13 = date(2026, 5, 13)


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        (
            NameFields(sender="WIR Bank", account="738.953-48.5800", title="Kontoeröffnung"),
            "2026.05.13 WIR Bank 738.953-48.5800 Kontoeröffnung",
        ),
        (
            NameFields(sender="Rega", title="Ihre Gönnerschaft 2026"),
            "2026.05.13 Rega Ihre Gönnerschaft 2026",
        ),
        (
            NameFields(
                sender="green", title="Rechnung", period="01.09.2026 - 30.09.2026", number="9291491"
            ),
            "2026.05.13 green Rechnung 01.09.2026 - 30.09.2026 9291491",
        ),
    ],
)
def test_assembles_in_template_order_skipping_empty_fields(
    fields: NameFields, expected: str
) -> None:
    assert assemble_name(MAY_13, fields) == expected


def test_a_missing_date_is_left_out_rather_than_failing() -> None:
    assert assemble_name(None, NameFields(sender="Rega", title="Spende")) == "Rega Spende"


def test_the_assembled_name_must_still_pass_the_filename_rules() -> None:
    with pytest.raises(ValidationError, match="'..'"):
        assemble_name(MAY_13, NameFields(sender="A", title="b .. c"))


class TestCleanFields:
    def test_trims_and_turns_blank_optional_fields_into_none(self) -> None:
        fields = clean_fields({"sender": " ZKB ", "title": "Abrechnung", "account": "  "})
        assert fields == NameFields(sender="ZKB", title="Abrechnung")

    @pytest.mark.parametrize("missing", ["sender", "title"])
    def test_requires_sender_and_title(self, missing: str) -> None:
        values = {"sender": "ZKB", "title": "Abrechnung", missing: ""}
        with pytest.raises(ValidationError, match=f"{naming.FIELD_LABELS[missing]} is required"):
            clean_fields(values)

    def test_names_the_field_with_a_forbidden_character(self) -> None:
        with pytest.raises(ValidationError, match="Period can't contain /"):
            clean_fields({"sender": "ZKB", "title": "Auszug", "period": "01/2026"})

    def test_rejects_control_characters(self) -> None:
        with pytest.raises(ValidationError, match="Number can't contain control"):
            clean_fields({"sender": "ZKB", "title": "Auszug", "number": "12\x0034"})


def test_the_model_is_asked_for_the_reasoning_and_date_before_the_fields() -> None:
    # Models write front to back; the reasoning and date that settle the fields come first.
    keys = list(ai.proposal_schema()["properties"])  # type: ignore[call-overload]
    assert keys[:2] == ["reasoning_text", "document_date"]
    assert keys.index("sender") < keys.index("title")
    assert "suggested_name" not in keys
    assert ai.SYSTEM_PROMPT.index("- document_date") < ai.SYSTEM_PROMPT.index("- sender")
