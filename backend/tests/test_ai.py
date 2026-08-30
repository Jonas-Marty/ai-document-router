"""Tests for proposal generation and, mostly, for rejecting bad model output.

SPEC 6.3: a proposal is either fully valid or it is a failure with a reason. A half-valid
proposal is worse than none, because it lands in the form pre-filled and pre-trusted.
"""

import json
from typing import Any

import httpx
import pytest

from app.services import ai
from app.services.ai import AIUnavailable, ProposalRejected, request_proposal

ROOTS = ["/Documents", "/Archive"]

VALID_REPLY = {
    "suggested_name": "2026-08-21_Swisscom_Rechnung",
    "target_folder_path": "/Documents/Finance/2026",
    "document_date": "2026-08-21",
    "confidence_score": 0.91,
    "reasoning_text": "Invoice header shows Swisscom, dated 21.08.2026.",
}


def completion(reply: dict[str, Any] | str) -> dict[str, Any]:
    content = reply if isinstance(reply, str) else json.dumps(reply)
    return {"choices": [{"message": {"content": content}}]}


def call(reply: dict[str, Any] | str, roots: list[str] | None = None) -> ai.Proposal:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion(reply))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return request_proposal(
            endpoint_url="https://ai.example.com/v1",
            model_name="gpt-4o",
            api_key="k",
            prompt="p",
            allowed_roots=ROOTS if roots is None else roots,
            client=client,
        )


class TestValidReply:
    def test_parses_every_field(self) -> None:
        proposal = call(VALID_REPLY)

        assert proposal.suggested_name == "2026-08-21_Swisscom_Rechnung"
        assert proposal.target_folder_path == "/Documents/Finance/2026"
        assert proposal.document_date is not None
        assert proposal.document_date.isoformat() == "2026-08-21"
        assert proposal.confidence_score == 0.91
        assert proposal.model_name == "gpt-4o"

    def test_accepts_a_null_date(self) -> None:
        assert call({**VALID_REPLY, "document_date": None}).document_date is None

    @pytest.mark.parametrize("value", ["null", "none", "None", "N/A", "n/a", "-", "  "])
    def test_treats_nullish_strings_as_no_date(self, value: str) -> None:
        """Smaller models routinely emit the *string* "null" rather than JSON null.
        Observed from llama3.1 against real documents; failing the whole proposal over it
        would discard an otherwise perfectly good filename and folder."""
        assert call({**VALID_REPLY, "document_date": value}).document_date is None

    def test_clamps_confidence_rather_than_rejecting(self) -> None:
        # SPEC 6.3 says clamp: a model saying 1.2 is confident, not broken.
        assert call({**VALID_REPLY, "confidence_score": 1.2}).confidence_score == 1.0
        assert call({**VALID_REPLY, "confidence_score": -0.5}).confidence_score == 0.0

    def test_normalizes_the_folder_path(self) -> None:
        proposal = call({**VALID_REPLY, "target_folder_path": "/Documents//Finance/"})

        assert proposal.target_folder_path == "/Documents/Finance"


class TestRejectsBadOutput:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("suggested_name", "invoice/../../etc/passwd"),
            ("suggested_name", "in|valid"),
            ("suggested_name", ""),
            ("suggested_name", "   "),
            ("suggested_name", 42),
            ("suggested_name", None),
            ("suggested_name", "x" * 201),
            ("suggested_name", ".hidden"),
            ("suggested_name", "trailing-"),
            ("suggested_name", "trailing."),
            ("suggested_name", "-leading"),
        ],
        ids=lambda v: f"name={v!r}"[:40],
    )
    def test_rejects_bad_names(self, field: str, value: object) -> None:
        with pytest.raises(ProposalRejected):
            call({**VALID_REPLY, field: value})

    def test_trims_surrounding_whitespace_rather_than_rejecting(self) -> None:
        """Whitespace is the model being sloppy, not the model being wrong. SPEC 7.1
        trims it; a trailing dot or hyphen is still a rejection."""
        assert call({**VALID_REPLY, "suggested_name": "  Invoice  "}).suggested_name == "Invoice"

    @pytest.mark.parametrize("name", ["invoice.pdf", "scan.PDF", "photo.png", "backup.tar.gz"])
    def test_rejects_a_name_carrying_an_extension(self, name: str) -> None:
        """The real extension is carried from the source file; accepting one here would
        produce 'invoice.pdf.pdf' after the move."""
        with pytest.raises(ProposalRejected, match="extension"):
            call({**VALID_REPLY, "suggested_name": name})

    @pytest.mark.parametrize(
        "name",
        [
            "2026.05.18 Reka Kartenersatz",
            "2026.01.13 Green Rechnung 01.01.2026 - 31.01.2026 9004761",
            "2026.02.01 SAC Schweizer Alpen-Club Spendenbescheinigung 2025",
            "2026.03.26 Microsoft Learn Credentials MartyJonas-7379",
        ],
    )
    def test_allows_dots_used_as_date_separators(self, name: str) -> None:
        """A real filing convention here is 'YYYY.MM.DD Sender Description'. Treating any
        dot as an extension rejected every proposal this user's model produced -- the
        mocked cases all used hyphens, so nothing caught it until real data did."""
        assert call({**VALID_REPLY, "suggested_name": name}).suggested_name == name

    @pytest.mark.parametrize(
        "folder",
        ["/etc/passwd", "/Secret/x", "relative/path", "", "/DocumentsSecret/x"],
        ids=lambda v: f"folder={v!r}",
    )
    def test_rejects_folders_outside_the_allowed_roots(self, folder: str) -> None:
        with pytest.raises(ProposalRejected):
            call({**VALID_REPLY, "target_folder_path": folder})

    def test_rejects_traversal_in_the_folder(self) -> None:
        with pytest.raises(ProposalRejected):
            call({**VALID_REPLY, "target_folder_path": "/Documents/../etc"})

    @pytest.mark.parametrize("date", ["21.08.2026", "not-a-date", "2026-13-45", 20260821])
    def test_rejects_unparseable_dates(self, date: object) -> None:
        with pytest.raises(ProposalRejected):
            call({**VALID_REPLY, "document_date": date})

    @pytest.mark.parametrize("score", ["high", None, True])
    def test_rejects_non_numeric_confidence(self, score: object) -> None:
        with pytest.raises(ProposalRejected):
            call({**VALID_REPLY, "confidence_score": score})

    def test_accepts_json_wrapped_in_a_code_fence(self) -> None:
        """Without response_format the model formats the reply its own way, and a markdown
        fence is the usual one. Rejecting it would fail a proposal that is entirely correct."""
        fenced = f"```json\n{json.dumps(VALID_REPLY)}\n```"

        assert call(fenced).suggested_name == VALID_REPLY["suggested_name"]

    def test_rejects_non_json_content(self) -> None:
        with pytest.raises(ProposalRejected, match="JSON"):
            call("I think this is an invoice, actually.")

    def test_rejects_a_json_array(self) -> None:
        with pytest.raises(ProposalRejected):
            call("[1, 2, 3]")

    def test_rejects_when_no_roots_are_configured(self) -> None:
        with pytest.raises(ProposalRejected, match="Settings"):
            call(VALID_REPLY, roots=[])

    def test_rejects_an_empty_choices_list(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": []})

        with (
            httpx.Client(transport=httpx.MockTransport(handler)) as client,
            pytest.raises(ProposalRejected),
        ):
            request_proposal(
                endpoint_url="https://ai.example.com/v1",
                model_name="m",
                api_key=None,
                prompt="p",
                allowed_roots=ROOTS,
                client=client,
            )


class TestTransport:
    def _call(self, handler: Any) -> ai.Proposal:
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return request_proposal(
                endpoint_url="https://ai.example.com/v1",
                model_name="m",
                api_key="k",
                prompt="p",
                allowed_roots=ROOTS,
                client=client,
            )

    def test_retries_once_on_5xx_then_succeeds(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            if len(calls) == 1:
                return httpx.Response(503)
            return httpx.Response(200, json=completion(VALID_REPLY))

        assert self._call(handler).suggested_name == VALID_REPLY["suggested_name"]
        assert len(calls) == 2

    def test_retries_once_on_timeout_then_succeeds(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            if len(calls) == 1:
                raise httpx.ConnectTimeout("slow")
            return httpx.Response(200, json=completion(VALID_REPLY))

        assert self._call(handler).confidence_score == 0.91
        assert len(calls) == 2

    def test_gives_up_after_the_second_5xx(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(500)

        with pytest.raises(AIUnavailable):
            self._call(handler)
        assert len(calls) == 2

    def test_does_not_retry_a_4xx(self) -> None:
        """A bad key or wrong model name fails identically twice; retrying only doubles
        the user's wait."""
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(401)

        with pytest.raises(AIUnavailable, match="Settings"):
            self._call(handler)
        assert len(calls) == 1

    def test_falls_back_when_the_endpoint_refuses_response_format(self) -> None:
        """Not every OpenAI-compatible server implements response_format; several answer 400
        rather than ignoring it, which failed every document against such an endpoint."""
        bodies: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            bodies.append(body)
            if "response_format" in body:
                return httpx.Response(400, json={"error": {"message": "response_format"}})
            return httpx.Response(200, json=completion(VALID_REPLY))

        assert self._call(handler).suggested_name == VALID_REPLY["suggested_name"]
        assert len(bodies) == 2
        assert bodies[1]["messages"] == bodies[0]["messages"]

    def test_reports_the_reason_the_endpoint_gave(self) -> None:
        """A 400 with no detail is unactionable: an unsupported field, a context overflow and
        an unknown model all read the same."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": {"message": "model 'm' does not exist"}})

        with pytest.raises(AIUnavailable, match="does not exist"):
            self._call(handler)

    def test_reports_a_non_json_rejection_body(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, text="Bad Request: unknown field")

        with pytest.raises(AIUnavailable, match="unknown field"):
            self._call(handler)

    def test_sends_the_key_and_targets_chat_completions(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=completion(VALID_REPLY))

        self._call(handler)

        assert str(seen[0].url) == "https://ai.example.com/v1/chat/completions"
        assert seen[0].headers["Authorization"] == "Bearer k"
        body = json.loads(seen[0].content)
        assert body["response_format"] == {"type": "json_object"}


class TestPrompt:
    def test_includes_tree_samples_and_hint(self) -> None:
        prompt = ai.build_prompt(
            "Swisscom invoice text",
            ["/Documents", "/Documents/Finance"],
            ["2026-01-01_Acme_Rechnung.pdf"],
            "YYYY-MM-DD_Sender_Type",
        )

        assert "/Documents/Finance" in prompt
        assert "2026-01-01_Acme_Rechnung.pdf" in prompt
        assert "YYYY-MM-DD_Sender_Type" in prompt
        assert "Swisscom invoice text" in prompt

    def test_caps_tree_depth(self) -> None:
        deep = "/a/b/c/d/e/f"
        assert deep not in ai.build_prompt("t", ["/a", deep], [], None)

    def test_caps_the_number_of_folders(self) -> None:
        """An unbounded tree pushes the request past the model's context window, which the
        endpoint reports as an opaque 400."""
        tree = [f"/Documents/{i:04d}" for i in range(ai.MAX_TREE_FOLDERS + 50)]

        prompt = ai.build_prompt("t", tree, [], None)

        assert prompt.count("/Documents/") == ai.MAX_TREE_FOLDERS
        assert "and 50 more folders" in prompt

    def test_keeps_shallow_folders_when_the_cap_bites(self) -> None:
        deep = [f"/Documents/a/{i:04d}" for i in range(ai.MAX_TREE_FOLDERS)]

        prompt = ai.build_prompt("t", ["/Documents", "/Archive", *deep], [], None)

        assert "/Archive" in prompt

    def test_caps_sample_filenames(self) -> None:
        names = [f"file-{i}.pdf" for i in range(20)]
        prompt = ai.build_prompt("t", ["/a"], names, None)

        assert prompt.count("file-") == ai.MAX_SAMPLE_FILENAMES


def models_response(payload: Any, status: int = 200) -> list[str]:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        assert request.headers["Authorization"] == "Bearer k"
        if isinstance(payload, str):
            return httpx.Response(status, text=payload)
        return httpx.Response(status, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return ai.list_models(endpoint_url="https://ai.example.com/v1", api_key="k", client=client)


def test_list_models_returns_sorted_unique_ids() -> None:
    models = models_response(
        {"data": [{"id": "gpt-4o"}, {"id": "claude"}, {"id": "gpt-4o"}, {"no_id": True}]}
    )

    assert models == ["claude", "gpt-4o"]


def test_list_models_accepts_an_endpoint_with_no_models() -> None:
    assert models_response({"data": []}) == []


def test_list_models_rejects_a_documentation_page() -> None:
    """The failure that prompted this: an endpoint URL pointing at HTML docs, not an API."""
    with pytest.raises(AIUnavailable, match="non-JSON"):
        models_response("<!doctype html><title>API docs</title>")


def test_list_models_reports_a_rejected_key_distinctly() -> None:
    with pytest.raises(AIUnavailable, match="rejected the API key"):
        models_response({"error": "nope"}, status=401)


def test_list_models_reports_an_http_error_with_the_status() -> None:
    with pytest.raises(AIUnavailable, match="404"):
        models_response({"error": "nope"}, status=404)


def test_list_models_rejects_a_reply_without_a_data_array() -> None:
    with pytest.raises(AIUnavailable, match="no 'data' array"):
        models_response({"models": ["gpt-4o"]})


def test_list_models_reports_an_unreachable_host() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AIUnavailable, match="Couldn't reach"):
            ai.list_models(endpoint_url="https://ai.example.com/v1", api_key=None, client=client)


def test_list_models_keeps_the_api_base_hint_for_a_non_json_error() -> None:
    """A wrong URL answers with an HTML page; quoting the markup would bury the one
    sentence that tells the user what to change."""
    with pytest.raises(AIUnavailable, match="not a documentation page"):
        models_response("<!doctype html><title>Not found</title>", status=404)


def test_list_models_reports_the_reason_the_endpoint_gave() -> None:
    with pytest.raises(AIUnavailable, match="tier does not include models"):
        models_response({"error": {"message": "tier does not include models"}}, status=403)
