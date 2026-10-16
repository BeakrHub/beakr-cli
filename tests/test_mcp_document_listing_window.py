"""Candidate pagination contract; no provider, backend, or native-agent calls."""

import pytest

from beakr_cli.document_listing import _NAVIGATION as NAVIGATION
from beakr_cli.document_listing import fit_document_listing as fit_listing


def listing(start=1, end=200, total=287, notices=(), row_length=100):
    rows = [
        f"matters/{i:05d}/{'x' * row_length}.docx  {{{{rag:{i:08x}}}}}  "
        f"(id 00000000-0000-0000-0000-{i:012d})"
        for i in range(start, end + 1)
    ]
    return (
        f"documents grep 'markman' (regex, ignoring case): {total} matching documents; "
        f"showing {start}-{end}. Searched 231 of 9288 documents (9288 in scope).\n"
        + "\n".join(
            [
                *notices,
                f"More documents follow: {total - end} after these. Continue with offset={end}.",
            ]
        )
        + "\n\n"
        + "\n".join(rows)
    )


def test_next_offset_follows_last_visible_row_not_requested_page_end():
    result = fit_listing(listing(), limit=20_000)
    assert result and len(result["content"]) <= 20_000
    end = result["coverage"]["visible_end"]
    assert 1 < end < 200
    assert f"showing 1-{end}." in result["content"]
    assert f"Continue with offset={end}." in result["content"]
    assert "offset=200." not in result["content"]
    assert f"{end:012d})" == result["content"][-13:]


def test_existing_offset_is_not_reset():
    result = fit_listing(listing(start=101, end=275), limit=5000)
    assert result["coverage"]["next_offset"] == 100 + result["coverage"]["returned"]
    assert "showing 101-" in result["content"]


def test_paging_by_visible_end_has_no_gaps_or_repeated_rows():
    offset, seen = 0, []
    while offset < 287:
        end = min(offset + 200, 287)
        result = fit_listing(listing(start=offset + 1, end=end), limit=5000)
        next_offset = result["coverage"]["next_offset"] if result else end
        seen.extend(range(offset + 1, next_offset + 1))
        offset = next_offset
    assert seen == list(range(1, 288))


def test_scan_limits_profiles_and_unindexed_warnings_survive():
    notices = (
        "Scan byte limit reached. Resume with after_document=abc.",
        "Documents marked [profile] are summaries, not original rows.",
        "3 documents have no indexed text.",
    )
    result = fit_listing(listing(notices=notices), limit=20_000)
    assert all(n in result["content"] for n in notices)
    assert not result["coverage"]["complete"]


def test_prefiltered_scan_summary_keeps_the_visible_window_and_scan_cursor():
    # The producer now distinguishes scanned candidates from considered scope;
    # rejecting that canonical summary would discard whole-row pagination.
    scope = (
        "Scanned 231 of 400 candidate documents after the safe prefilter; "
        "9288 documents considered (9288 in scope)."
    )
    warning = "Scan byte limit reached. Resume with after_document=scan-cursor."
    text = listing(notices=(warning,)).replace(
        "Searched 231 of 9288 documents (9288 in scope).", scope
    )
    result = fit_listing(text, limit=5000)
    assert result is not None
    assert 0 < result["coverage"]["returned"] < 200
    assert scope in result["content"] and warning in result["content"]
    assert result["coverage"]["next_offset"] == result["coverage"]["returned"]
    assert "before advancing any after_document scan cursor" in result["content"]
    assert len(result["content"]) <= 5000


def test_rows_tokens_and_paths_are_unchanged():
    text = listing()
    result = fit_listing(text, limit=20_000)
    before = text.split("\n\n")[1].splitlines()
    after = result["content"].split("\n\n")[1].splitlines()
    assert after == before[: len(after)]


def test_api_navigation_footer_is_preserved_inside_budget():
    result = fit_listing(listing() + "\n\n" + NAVIGATION, limit=20_000)
    assert NAVIGATION in result["content"]
    assert len(result["content"]) <= 20_000


def test_a_window_that_already_fits_is_unchanged():
    assert fit_listing(listing(end=2), limit=20_000) is None


@pytest.mark.parametrize(
    "text",
    [
        "\n\n" + "unknown" * 5000,
        listing().replace("showing 1-200", "showing 1-199"),
        listing() + "\n[truncated: 10 more characters]",
        listing().replace("matching documents", "matching lines in 8 documents"),
        listing().replace("(id 00000000-0000-0000-0000-000000000001)", "(id invalid)"),
        listing().replace("\n\n", "\nUnexpected shape\n", 1),
        listing() + "\n\nUnknown future navigation rule.",
    ],
)
def test_unknown_or_inconsistent_shape_keeps_generic_recovery(text):
    assert fit_listing(text, limit=2000) is None


def test_no_partial_row_when_even_one_row_will_not_fit():
    assert fit_listing(listing(row_length=3000), limit=1000) is None


def test_backend_budget_window_is_accepted_only_with_matching_continuation():
    text = listing(end=150).replace("showing 1-150", "showing 1-200")
    result = fit_listing(text, limit=5000)
    assert result["coverage"]["backend_rendered_end"] == 150
    assert result["coverage"]["backend_requested_end"] == 200
    assert fit_listing(text.replace("offset=150.", "offset=151."), limit=5000) is None


def test_readable_citation_rows_preserve_paths_and_ids():
    import re

    text = re.sub(r"  \{\{rag:[^}]+\}\}", "", listing())
    result = fit_listing(text, limit=5000)
    assert result and "{{" not in result["content"]
    assert (
        result["content"].split("\n\n")[1].splitlines()
        == text.split("\n\n")[1].splitlines()[: result["coverage"]["returned"]]
    )


def test_finish_keeps_status_sources_unknown_coverage_and_hints_without_mutation():
    from copy import deepcopy

    from beakr_cli.mcp_result import finish

    original = {
        "content": listing(),
        "sources": [{"title": "source"}],
        "status": "ok",
        "request_id": "request-1",
        "coverage": {"custom_scan_limit": True, "next_cursor": "scan-cursor"},
        "next": ["Original scope warning."],
    }
    before = deepcopy(original)
    result = finish(original, tool="documents")
    assert original == before
    assert result["sources"] == before["sources"]
    assert result["status"] == "ok" and result["request_id"] == "request-1"
    assert result["coverage"]["custom_scan_limit"] is True
    assert result["coverage"]["next_cursor"] is None
    assert result["coverage"]["upstream_next_cursor"] == "scan-cursor"
    assert "Original scope warning." in result["next"]
    assert finish(result, tool="documents") == result


def test_errors_and_unknown_shapes_keep_generic_cut_recovery():
    from beakr_cli.mcp_result import finish

    for body in (
        {"content": listing(), "status": "error"},
        {"content": listing(), "is_error": True},
        {"content": "unrecognized" * 5000},
    ):
        result = finish(body, tool="documents")
        assert "[truncated:" in result["content"]
        assert "next_offset" not in result["coverage"]
