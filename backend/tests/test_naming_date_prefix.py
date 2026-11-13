from datetime import date

import pytest

from app.services import ai
from app.services.naming import align_date_prefix

PATTERN = r"^\d{4}\.\d{2}\.\d{2} .+"
APRIL_16 = date(2026, 4, 16)


@pytest.mark.parametrize(
    "name",
    [
        "2026 Helvetia Prämienrechnung",
        "2026.04 Helvetia Prämienrechnung",
        "2026-04-16 Helvetia Prämienrechnung",
        "16.04.2026 Helvetia Prämienrechnung",
        "Helvetia Prämienrechnung",
    ],
)
def test_a_missing_or_partial_date_prefix_is_completed_from_the_document_date(
    name: str,
) -> None:
    assert align_date_prefix(name, APRIL_16, PATTERN) == "2026.04.16 Helvetia Prämienrechnung"


def test_the_users_date_format_is_kept() -> None:
    assert (
        align_date_prefix("2026 Helvetia", APRIL_16, r"^\d{4}-\d{2}-\d{2} ")
        == "2026-04-16 Helvetia"
    )


@pytest.mark.parametrize(
    ("name", "document_date", "pattern"),
    [
        ("2026.03.01 Helvetia", APRIL_16, PATTERN),  # already matches: the model's call
        ("2026 Helvetia", None, PATTERN),  # no date to use
        ("2026 Helvetia", APRIL_16, None),  # no pattern: nothing says a prefix is wanted
        ("2026 Helvetia", APRIL_16, r"^[A-Z]+_"),  # dating it wouldn't make it match
        ("2026 Helvetia", APRIL_16, r"(unclosed"),  # a pattern that doesn't compile
    ],
)
def test_the_name_is_left_alone_otherwise(
    name: str, document_date: date | None, pattern: str | None
) -> None:
    assert align_date_prefix(name, document_date, pattern) == name


def test_the_reasoning_says_the_name_was_dated() -> None:
    proposal = ai.Proposal(
        suggested_name="2026 Helvetia",
        target_folder_path="/Documents",
        document_date=APRIL_16,
        confidence_score=0.9,
        reasoning_text="Premium invoice.",
        model_name="m",
    )

    fixed = ai.with_date_prefix(proposal, PATTERN)

    assert fixed.suggested_name == "2026.04.16 Helvetia"
    assert fixed.reasoning_text.endswith("(Name dated from the document date.)")


def test_the_model_is_asked_for_the_date_before_the_name() -> None:
    # Models write front to back; asked for the name first, they wrote "2026 ..." and the
    # full date only afterwards.
    keys = list(ai.proposal_schema()["properties"])  # type: ignore[call-overload]
    assert keys.index("document_date") < keys.index("suggested_name")
    assert keys.index("reasoning_text") < keys.index("suggested_name")
    assert ai.SYSTEM_PROMPT.index("- document_date") < ai.SYSTEM_PROMPT.index("- suggested_name")
