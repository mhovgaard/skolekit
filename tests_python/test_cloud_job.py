from skoleklar.runtime.scan import _dashboard_failure_code, _safe_failure_code, _stage_summary


def test_stage_summary_exposes_only_safe_counts() -> None:
    output = (
        {
            "candidate_count": 7,
            "failures": 2,
            "results": [{"source_key": "private", "error": "private detail"}],
        }
    )

    assert _stage_summary("derive", output) == {
        "candidate_count": 7,
        "failures": 2,
    }


def test_failure_code_redacts_provider_error_detail() -> None:
    output = (
        {
            "error": "OpenRouter Chat Completions API returned 429: private detail"
        }
    )

    assert _safe_failure_code(output) == "provider_http_429"


def test_dashboard_failure_code_never_needs_model_content() -> None:
    assert _dashboard_failure_code(ValueError(
        "Dashboard composition failed validation: ValueError: "
        "dashboard dates must follow the selected school week"
    )) == "wrong_school_week"
    assert _dashboard_failure_code(ValueError(
        "Dashboard composition failed validation: ValueError: unexpected"
    )) == "composition_validation"


def test_validation_reason_keeps_fixed_messages_and_drops_quoted_values() -> None:
    from skoleklar.dashboard_composition import validation_reason

    assert validation_reason(ValueError(
        "weekend activities must use the fixed weekend area"
    )) == "weekend activities must use the fixed weekend area"
    assert validation_reason(ValueError(
        "display strings must be bounded single-line text: weekend[].text length 52 "
        "exceeds 48 or contains control characters"
    )).startswith("display strings must be bounded single-line text: weekend[].text")
    # Python quotes the offending value, which may be model or school text.
    assert validation_reason(ValueError("Invalid isoformat string: 'Onsdag 4. nov'")) == "ValueError"
    assert validation_reason(KeyError("state")) == "KeyError: state"
    assert validation_reason(KeyError("fact-12")) == "KeyError: fact-12"
    assert validation_reason(KeyError("Anna har fødselsdag")) == "KeyError"
    assert validation_reason(TypeError("'NoneType' object is not subscriptable")) == "TypeError"


def test_consolidate_summary_exposes_repair_counts_only() -> None:
    output = (
        {
            "status": "complete",
            "input_count": 66,
            "output_count": 60,
            "reduction_count": 6,
            "omitted_id_count": 2,
            "dropped_group_count": 1,
            "unknown_id_count": 0,
            "repeated_id_count": 1,
            "items": [{"title": "private detail"}],
        }
    )

    assert _stage_summary("consolidate", output) == {
        "input_count": 66,
        "output_count": 60,
        "reduction_count": 6,
        "dropped_group_count": 1,
        "unknown_id_count": 0,
        "repeated_id_count": 1,
        "omitted_id_count": 2,
    }
