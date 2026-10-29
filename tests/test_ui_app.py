"""Streamlit AppTest coverage for the complete judge-facing workflow."""

from __future__ import annotations

from streamlit.testing.v1 import AppTest


def _app() -> AppTest:
    return AppTest.from_file("app.py", default_timeout=90).run()


def _button(at: AppTest, label: str):
    matches = [button for button in at.button if button.label == label]
    assert matches, f"Button not found: {label}"
    return matches[0]


def _run_demo(at: AppTest) -> None:
    _button(at, "Run demo analysis").click()
    at.run()
    assert not at.exception


def test_initial_page_and_button_navigation():
    at = _app()
    assert not at.exception
    assert [title.value for title in at.title] == ["Analyze a contract"]

    _run_demo(at)
    assert at.session_state["result"].summary.confirmed_conflicts == 4
    assert at.session_state["nav"] == "Analyze contract"

    at.radio(key="nav").set_value("Risk dashboard").run()
    assert not at.exception
    assert at.session_state["nav"] == "Risk dashboard"
    assert [title.value for title in at.title] == ["Risk dashboard"]


def test_radio_navigation_evidence_and_review_save_clear():
    at = _app()
    _run_demo(at)

    at.radio(key="nav").set_value("Risk dashboard").run()
    assert not at.exception
    assert [title.value for title in at.title] == ["Risk dashboard"]

    at.radio(key="nav").set_value("Evidence explorer").run()
    assert not at.exception
    assert [title.value for title in at.title] == ["Evidence explorer"]

    at.radio(key="nav").set_value("Review & export").run()
    assert not at.exception
    assert [title.value for title in at.title] == ["Review & export"]
    assert at.session_state["review_store"] == {}

    decision = next(radio for radio in at.radio if radio.key.endswith("_decision_F0001"))
    notes = next(area for area in at.text_area if area.key.endswith("_notes_F0001"))
    reviewer = next(field for field in at.text_input if field.key == "reviewer_name")
    decision.set_value("accept")
    notes.set_value("Request 30-day payment terms.")
    reviewer.set_value("Judge Demo")
    next(button for button in at.button if button.key.endswith("_save_F0001")).click()
    at.run()

    assert not at.exception
    entry = at.session_state["review_store"]["F0001"]
    assert entry["decision"] == "accept"
    assert entry["notes"] == "Request 30-day payment terms."
    assert entry["reviewer"] == "Judge Demo"
    assert any(metric.label == "Decisions recorded" for metric in at.metric)

    next(button for button in at.button if button.key.endswith("_clear_F0001")).click()
    at.run()
    assert not at.exception
    assert at.session_state["review_store"] == {}
    cleared = next(radio for radio in at.radio if radio.key.endswith("_decision_F0001"))
    cleared_notes = next(area for area in at.text_area if area.key.endswith("_notes_F0001"))
    assert cleared.value == "not_reviewed"
    assert cleared_notes.value == ""


def test_policy_edit_save_and_rerun():
    at = _app()
    _run_demo(at)

    at.radio(key="nav").set_value("Policy settings").run()
    assert not at.exception
    next(
        field for field in at.number_input if field.label.startswith("Maximum payment")
    ).set_value(120)
    _button(at, "Save policy").click()
    at.run()
    assert not at.exception
    assert at.session_state["policy"].max_payment_days == 120

    _button(at, "Re-run analysis").click()
    at.run()
    assert not at.exception
    assert at.session_state["nav"] == "Risk dashboard"
    result = at.session_state["result"]
    assert result.policy.max_payment_days == 120
    assert result.summary.confirmed_conflicts == 3
    assert any(finding.rule_id == "payment.above_preference" for finding in result.findings)
