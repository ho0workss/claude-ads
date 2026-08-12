from __future__ import annotations

import copy
from pathlib import Path

import pytest

from claude_ads_core.adapters import GenericCSVExportAdapter
from claude_ads_core.contracts import ContractError, validate_contract
from claude_ads_core.performance import (
    COMPONENT_WEIGHTS,
    COMPONENT_WEIGHT_TOTAL,
    PerformanceError,
    score_performance,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"
MULTI_CAMPAIGN = FIXTURE_ROOT / "performance" / "multi-campaign.csv"
SINGLE_ROW = FIXTURE_ROOT / "exports" / "google.csv"

FULL_TARGETS = {"target_cpa": 15.0, "currency": "USD", "budget_basis": "daily"}


def facts(path: Path = MULTI_CAMPAIGN, platform: str = "google") -> dict:
    return GenericCSVExportAdapter(platform).read_facts(path)


def component(scorecard: dict, name: str) -> dict:
    return next(item for item in scorecard["components"] if item["component"] == name)


def test_component_weights_sum_to_one_hundred() -> None:
    assert sum(COMPONENT_WEIGHTS.values()) == COMPONENT_WEIGHT_TOTAL


def test_scorecard_is_contract_valid_and_deterministic() -> None:
    payload = facts()
    first = score_performance(payload, FULL_TARGETS).to_dict()
    second = score_performance(copy.deepcopy(payload), dict(FULL_TARGETS)).to_dict()
    validate_contract("performance-scorecard", first)
    assert first == second


def test_golden_multi_campaign_scorecard() -> None:
    scorecard = score_performance(facts(), FULL_TARGETS).to_dict()

    assert scorecard["status"] == "graded"
    assert scorecard["input_coverage"] == 100.0
    assert scorecard["performance_score"] == 71.83
    assert scorecard["grade"] == "fair"
    assert scorecard["totals"] == {
        "spend": 504.0,
        "conversions": 27.0,
        "cpa": 18.67,
        "available_budget": 630.0,
        "pacing_basis": "delivering-daily-budget",
        "campaign_count": 3,
        "day_count": 3,
    }
    assert component(scorecard, "efficiency")["score"] == 75.56
    assert component(scorecard, "waste_concentration")["score"] == 76.19
    assert component(scorecard, "pacing")["score"] == 60.0


def test_daily_budget_is_counted_once_per_campaign_day() -> None:
    """The fixture repeats one campaign-day budget across two creative rows."""

    scorecard = score_performance(facts(), FULL_TARGETS).to_dict()
    efficient = next(item for item in scorecard["campaigns"] if item["campaign_id"] == "perf-c1")

    # Three days at 100.00, not six creative rows at 100.00.
    assert efficient["budget_total"] == 300.0
    assert efficient["spend"] == 294.0
    assert efficient["budget_utilization"] == 98.0


def test_campaign_level_conversions_and_cpa_survive_normalization() -> None:
    scorecard = score_performance(facts(), FULL_TARGETS).to_dict()
    by_id = {item["campaign_id"]: item for item in scorecard["campaigns"]}

    assert by_id["perf-c1"]["cpa"] == 12.25
    assert by_id["perf-c2"]["conversions"] == 0.0
    assert by_id["perf-c2"]["cpa"] is None
    assert by_id["perf-c2"]["target_cpa_ratio"] is None
    assert by_id["perf-c3"]["target_cpa_ratio"] == 2.0
    assert sum(item["spend_share"] for item in scorecard["campaigns"]) == pytest.approx(100.0, abs=0.02)


def test_missing_target_cpa_leaves_efficiency_unknown_and_lowers_coverage() -> None:
    scorecard = score_performance(facts(), {"currency": "USD", "budget_basis": "daily"}).to_dict()

    efficiency = component(scorecard, "efficiency")
    assert efficiency["status"] == "unknown"
    assert efficiency["score"] is None
    assert "targets.target_cpa" in scorecard["missing_inputs"]
    # Efficiency carries 50 of 100, so coverage halves and the score is withheld.
    assert scorecard["input_coverage"] == 50.0
    assert scorecard["status"] == "insufficient_evidence"
    assert scorecard["performance_score"] is None
    assert scorecard["grade"] is None


def test_no_targets_withholds_the_score_entirely() -> None:
    scorecard = score_performance(facts()).to_dict()

    validate_contract("performance-scorecard", scorecard)
    assert scorecard["performance_score"] is None
    assert scorecard["status"] == "insufficient_evidence"
    assert scorecard["missing_inputs"] == [
        "targets.target_cpa",
        "targets.planned_spend or targets.budget_basis",
    ]


def test_partial_targets_produce_a_provisional_score() -> None:
    scorecard = score_performance(facts(), {"target_cpa": 15.0, "currency": "USD"}).to_dict()

    assert component(scorecard, "pacing")["status"] == "unknown"
    assert scorecard["totals"]["pacing_basis"] == "undeclared"
    assert scorecard["input_coverage"] == 75.0
    assert scorecard["status"] == "provisional"
    assert scorecard["performance_score"] is not None


def test_undeclared_budget_basis_reports_no_available_budget() -> None:
    """A budget column alone cannot say whether it is daily or lifetime."""

    scorecard = score_performance(facts(), {"target_cpa": 15.0, "currency": "USD"}).to_dict()

    assert scorecard["totals"]["available_budget"] is None
    assert all(item["budget_total"] is None for item in scorecard["campaigns"])


def test_lifetime_basis_takes_the_largest_value_instead_of_summing() -> None:
    daily = score_performance(facts(), FULL_TARGETS).to_dict()
    lifetime = score_performance(
        facts(), {**FULL_TARGETS, "budget_basis": "lifetime"}
    ).to_dict()

    assert daily["totals"]["available_budget"] == 630.0
    # One campaign-day figure per campaign: 100 + 50 + 60.
    assert lifetime["totals"]["available_budget"] == 210.0
    assert lifetime["totals"]["pacing_basis"] == "declared-lifetime-budget"


def test_declared_planned_spend_overrides_export_budget() -> None:
    scorecard = score_performance(
        facts(), {"target_cpa": 15.0, "currency": "USD", "planned_spend": 504.0}
    ).to_dict()

    pacing = component(scorecard, "pacing")
    assert scorecard["totals"]["pacing_basis"] == "declared-planned-spend"
    assert pacing["measured_value"] == 100.0
    assert pacing["score"] == 100.0


def test_pacing_penalizes_overspend_and_underspend_symmetrically() -> None:
    under = score_performance(
        facts(), {"target_cpa": 15.0, "currency": "USD", "planned_spend": 630.0}
    ).to_dict()
    over = score_performance(
        facts(), {"target_cpa": 15.0, "currency": "USD", "planned_spend": 420.0}
    ).to_dict()

    assert component(under, "pacing")["measured_value"] == 80.0
    assert component(over, "pacing")["measured_value"] == 120.0
    assert component(under, "pacing")["score"] == component(over, "pacing")["score"] == 60.0


def test_zero_conversions_with_spend_scores_efficiency_zero_not_unknown() -> None:
    payload = facts()
    for row in payload["rows"]:
        row["conversions"] = 0

    scorecard = score_performance(payload, FULL_TARGETS).to_dict()
    efficiency = component(scorecard, "efficiency")

    assert efficiency["status"] == "scored"
    assert efficiency["score"] == 0.0
    assert scorecard["totals"]["cpa"] is None
    assert component(scorecard, "waste_concentration")["score"] == 0.0


def test_zero_spend_makes_delivery_components_not_applicable() -> None:
    payload = facts()
    for row in payload["rows"]:
        row["spend"] = 0

    scorecard = score_performance(payload, FULL_TARGETS).to_dict()

    validate_contract("performance-scorecard", scorecard)
    assert component(scorecard, "efficiency")["status"] == "not_applicable"
    assert component(scorecard, "waste_concentration")["status"] == "not_applicable"
    assert scorecard["performance_score"] is None
    assert scorecard["status"] == "insufficient_evidence"


def test_efficiency_reaches_zero_at_double_the_target_and_clamps() -> None:
    payload = facts()
    at_double = score_performance(payload, {**FULL_TARGETS, "target_cpa": 9.333333}).to_dict()
    far_worse = score_performance(payload, {**FULL_TARGETS, "target_cpa": 1.0}).to_dict()

    assert component(at_double, "efficiency")["score"] == pytest.approx(0.0, abs=0.05)
    assert component(far_worse, "efficiency")["score"] == 0.0


def test_efficiency_clamps_at_one_hundred_when_target_is_beaten() -> None:
    scorecard = score_performance(facts(), {**FULL_TARGETS, "target_cpa": 1000.0}).to_dict()

    assert component(scorecard, "efficiency")["score"] == 100.0


def test_budget_constrained_efficient_campaign_is_an_unscored_opportunity() -> None:
    scorecard = score_performance(facts(), FULL_TARGETS).to_dict()

    assert [item["campaign_id"] for item in scorecard["opportunities"]] == ["perf-c1"]
    assert scorecard["opportunities"][0]["kind"] == "budget-constrained-efficient-campaign"
    # Opportunities never move the score: it stays the weighted component result.
    assert scorecard["performance_score"] == 71.83


def test_opportunities_require_a_declared_target() -> None:
    scorecard = score_performance(facts(), {"currency": "USD", "budget_basis": "daily"}).to_dict()

    assert scorecard["opportunities"] == []


def test_zero_conversion_spend_emits_a_conversion_lag_caveat() -> None:
    scorecard = score_performance(facts(), FULL_TARGETS).to_dict()

    assert any("conversion lag" in caveat for caveat in scorecard["caveats"])


def test_single_day_window_emits_a_trend_caveat() -> None:
    scorecard = score_performance(facts(SINGLE_ROW), {"target_cpa": 4.0, "currency": "USD"}).to_dict()

    assert any("fewer than two distinct days" in caveat for caveat in scorecard["caveats"])
    assert scorecard["totals"]["day_count"] == 1


def test_mismatched_target_currency_is_rejected() -> None:
    with pytest.raises(PerformanceError, match="targets.currency does not match"):
        score_performance(facts(), {"target_cpa": 30000, "currency": "KRW"})


@pytest.mark.parametrize("field", ["target_cpa", "planned_spend"])
@pytest.mark.parametrize("value", [0, -1, "abc", True])
def test_non_positive_or_non_numeric_targets_are_rejected(field: str, value: object) -> None:
    with pytest.raises(PerformanceError):
        score_performance(facts(), {field: value, "currency": "USD"})


def test_unknown_budget_basis_is_rejected() -> None:
    with pytest.raises(PerformanceError, match="targets.budget_basis"):
        score_performance(facts(), {"target_cpa": 15.0, "budget_basis": "monthly"})


def test_non_mapping_targets_are_rejected() -> None:
    with pytest.raises(PerformanceError, match="targets must be an object"):
        score_performance(facts(), [("target_cpa", 15.0)])  # type: ignore[arg-type]


def test_invalid_facts_payload_is_rejected() -> None:
    payload = facts()
    del payload["rows"][0]["spend"]

    with pytest.raises(PerformanceError):
        score_performance(payload, FULL_TARGETS)


def test_duplicate_row_grain_is_rejected() -> None:
    payload = facts()
    payload["rows"].append(dict(payload["rows"][0]))

    with pytest.raises(PerformanceError, match="duplicates an additive row grain"):
        score_performance(payload, FULL_TARGETS)


def test_row_outside_the_declared_window_is_rejected() -> None:
    payload = facts()
    payload["rows"][0]["date"] = "2026-08-01"

    with pytest.raises(PerformanceError, match="falls outside"):
        score_performance(payload, FULL_TARGETS)


def test_facts_reader_matches_snapshot_totals() -> None:
    adapter = GenericCSVExportAdapter("google")
    snapshot = adapter.read_snapshot(MULTI_CAMPAIGN)
    payload = adapter.read_facts(MULTI_CAMPAIGN)

    assert payload["account"] == snapshot["account"]
    assert payload["window"] == snapshot["window"]
    assert payload["currency"] == snapshot["currency"]
    assert sum(row["spend"] for row in payload["rows"]) == snapshot["spend"]


def test_facts_rows_are_ordered_deterministically() -> None:
    payload = facts()
    keys = [(row["date"], row["campaign_id"], row["creative_id"]) for row in payload["rows"]]

    assert keys == sorted(keys)


def test_scorecard_contract_rejects_a_grade_without_a_score() -> None:
    scorecard = score_performance(facts(), FULL_TARGETS).to_dict()
    scorecard["performance_score"] = None

    with pytest.raises(ContractError, match="\\$.grade must be null"):
        validate_contract("performance-scorecard", scorecard)


def test_scorecard_contract_rejects_a_score_under_insufficient_evidence() -> None:
    scorecard = score_performance(facts(), FULL_TARGETS).to_dict()
    scorecard["status"] = "insufficient_evidence"

    with pytest.raises(ContractError, match="must be null when coverage is insufficient"):
        validate_contract("performance-scorecard", scorecard)


@pytest.mark.parametrize("field", ["campaign_count", "day_count"])
@pytest.mark.parametrize("invalid", [True, False, 1.5, 3.0, "3", None, -1])
def test_scorecard_contract_enforces_integer_counts(field: str, invalid: object) -> None:
    """The schema declares these as integers, so the validator must agree."""

    scorecard = score_performance(facts(), FULL_TARGETS).to_dict()
    scorecard["totals"][field] = invalid

    with pytest.raises(ContractError, match="must be an integer|must be >="):
        validate_contract("performance-scorecard", scorecard)


@pytest.mark.parametrize("field", ["spend", "conversions", "cpa", "available_budget"])
def test_scorecard_contract_rejects_negative_totals(field: str) -> None:
    scorecard = score_performance(facts(), FULL_TARGETS).to_dict()
    scorecard["totals"][field] = -1

    with pytest.raises(ContractError, match="must be >= 0"):
        validate_contract("performance-scorecard", scorecard)


def test_scorecard_contract_rejects_a_scored_component_without_a_score() -> None:
    scorecard = score_performance(facts(), FULL_TARGETS).to_dict()
    component(scorecard, "efficiency")["score"] = None

    with pytest.raises(ContractError, match="\\$.components\\[0\\].score"):
        validate_contract("performance-scorecard", scorecard)
