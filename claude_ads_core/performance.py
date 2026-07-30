"""Deterministic performance scoring against operator-declared targets.

The grading basis is the operator's own declared target and the account's own
normalized export. No component encodes a platform-behavior claim, a benchmark,
or an industry threshold, so this engine is independent of the source-gated
control registry and of the disabled account-health scoring profiles.

Performance score and account-health score are separate outputs. A performance
score never satisfies, substitutes for, or implies an account-health grade, and
the two must not be combined into one number.

Every normalization convention used here is disclosed in the emitted scorecard
so the operator can audit the mapping instead of trusting the number. Raw
ratios accompany every derived score for the same reason.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping, Sequence

from .contracts import ContractError, validate_contract

SCORING_CONVENTION = "operator-target-linear-v1"
GRADE_SCALE = "operator-target-band-v1"

#: Component weights must sum to exactly 100. ``efficiency`` carries the
#: majority because it is the only component measured directly against the
#: operator's declared target; the others measure allocation and delivery.
COMPONENT_WEIGHTS: Mapping[str, Decimal] = {
    "efficiency": Decimal("50"),
    "waste_concentration": Decimal("25"),
    "pacing": Decimal("25"),
}
COMPONENT_WEIGHT_TOTAL = Decimal("100")

#: Cost ratio at which efficiency reaches zero. A ratio of 1.0 means actual CPA
#: equals the declared target; 2.0 means double the target.
EFFICIENCY_ZERO_RATIO = Decimal("2")
#: Absolute budget-utilization deviation at which pacing reaches zero, applied
#: symmetrically so under-delivery and overspend are both penalized.
PACING_ZERO_DEVIATION = Decimal("0.5")

NORMAL_COVERAGE = Decimal("80")
PROVISIONAL_COVERAGE = Decimal("60")

#: Budget interpretations an operator may declare. An export cannot reveal
#: which one applies, so pacing stays unknown until one is stated.
BUDGET_BASES = {"daily", "lifetime"}

GRADE_BANDS: tuple[tuple[Decimal, str], ...] = (
    (Decimal("90"), "excellent"),
    (Decimal("75"), "good"),
    (Decimal("60"), "fair"),
    (Decimal("40"), "poor"),
)
LOWEST_GRADE = "critical"


class PerformanceError(ValueError):
    """Raised for invalid or internally inconsistent performance inputs."""


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise PerformanceError(f"{field} must be numeric")
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise PerformanceError(f"{field} must be numeric") from exc
    if not result.is_finite():
        raise PerformanceError(f"{field} must be finite")
    return result


def _rounded(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _clamped_percent(value: Decimal) -> Decimal:
    if value < Decimal("0"):
        return Decimal("0")
    if value > Decimal("100"):
        return Decimal("100")
    return value


def _coverage_status(coverage: Decimal) -> str:
    if coverage >= NORMAL_COVERAGE:
        return "graded"
    if coverage >= PROVISIONAL_COVERAGE:
        return "provisional"
    return "insufficient_evidence"


def _grade(score: Decimal) -> str:
    for threshold, label in GRADE_BANDS:
        if score >= threshold:
            return label
    return LOWEST_GRADE


@dataclass(frozen=True)
class ComponentScore:
    """One scored dimension, or an explicit reason it could not be scored."""

    component: str
    weight: float
    score: float | None
    status: str
    basis: str
    observation: str
    measured_value: float | None = None
    target_value: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CampaignPerformance:
    campaign_id: str
    name: str
    status: str
    spend: float
    conversions: float
    cpa: float | None
    spend_share: float
    budget_total: float | None
    budget_utilization: float | None
    target_cpa_ratio: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PerformanceScorecard:
    schema_version: str
    account: dict[str, Any]
    window: dict[str, str]
    currency: str
    performance_score: float | None
    grade: str | None
    status: str
    input_coverage: float
    scoring_convention: str
    grade_scale: str
    grading_basis: str
    totals: dict[str, Any]
    components: tuple[ComponentScore, ...]
    campaigns: tuple[CampaignPerformance, ...]
    daily_spend: tuple[dict[str, Any], ...]
    opportunities: tuple[dict[str, Any], ...]
    missing_inputs: tuple[str, ...]
    caveats: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "account": self.account,
            "window": self.window,
            "currency": self.currency,
            "performance_score": self.performance_score,
            "grade": self.grade,
            "status": self.status,
            "input_coverage": self.input_coverage,
            "scoring_convention": self.scoring_convention,
            "grade_scale": self.grade_scale,
            "grading_basis": self.grading_basis,
            "totals": self.totals,
            "components": [component.to_dict() for component in self.components],
            "campaigns": [campaign.to_dict() for campaign in self.campaigns],
            "daily_spend": list(self.daily_spend),
            "opportunities": list(self.opportunities),
            "missing_inputs": list(self.missing_inputs),
            "caveats": list(self.caveats),
        }


def _validated_targets(targets: Mapping[str, Any] | None, currency: str) -> dict[str, Decimal | None]:
    """Accept only operator-declared targets, and never infer a missing one."""

    if targets is None:
        return {"target_cpa": None, "planned_spend": None, "budget_basis": None}
    if not isinstance(targets, Mapping):
        raise PerformanceError("targets must be an object")

    declared_currency = targets.get("currency")
    if declared_currency is not None:
        if not isinstance(declared_currency, str) or not declared_currency.strip():
            raise PerformanceError("targets.currency must be a non-empty string")
        if declared_currency.strip().upper() != currency:
            raise PerformanceError(
                "targets.currency does not match export currency: "
                f"{declared_currency.strip().upper()} vs {currency}"
            )

    resolved: dict[str, Any] = {}
    for field in ("target_cpa", "planned_spend"):
        raw = targets.get(field)
        if raw is None:
            resolved[field] = None
            continue
        value = _decimal(raw, f"targets.{field}")
        if value <= 0:
            raise PerformanceError(f"targets.{field} must be greater than zero")
        resolved[field] = value

    # An export's budget column cannot distinguish a daily budget from a
    # lifetime budget: both repeat per row. Summing a lifetime budget across
    # days would inflate the pacing denominator, so the basis must be declared
    # rather than guessed.
    basis = targets.get("budget_basis")
    if basis is not None:
        if basis not in BUDGET_BASES:
            raise PerformanceError(f"targets.budget_basis must be one of: {', '.join(sorted(BUDGET_BASES))}")
    resolved["budget_basis"] = basis
    return resolved


def _campaign_budget(
    grain_budget: Mapping[tuple[str, str], Decimal],
    budget_basis: str | None,
) -> dict[str, Decimal]:
    """Reduce campaign-day budgets to one budget figure per campaign.

    A daily basis sums each campaign's observed days. A lifetime basis takes
    the largest observed value, because the same campaign total repeats on
    every row. With no declared basis the reduction is undefined and no
    campaign budget is reported.
    """

    if budget_basis is None:
        return {}
    reduced: dict[str, Decimal] = {}
    for (campaign_id, _), budget in grain_budget.items():
        if budget_basis == "daily":
            reduced[campaign_id] = reduced.get(campaign_id, Decimal("0")) + budget
        else:
            reduced[campaign_id] = max(reduced.get(campaign_id, Decimal("0")), budget)
    return reduced


def _pacing_reference(
    planned_spend: Decimal | None,
    budget_basis: str | None,
    grain_spend: Mapping[tuple[str, str], Decimal],
    grain_budget: Mapping[tuple[str, str], Decimal],
) -> tuple[Decimal | None, str]:
    """Resolve the spend obligation pacing is measured against.

    A declared planned spend wins because it is unambiguous. Otherwise a daily
    basis counts only campaign-days that actually delivered spend, so a fully
    paused day is not counted as an unmet obligation. A lifetime basis uses each
    campaign's largest observed budget.
    """

    if planned_spend is not None:
        return planned_spend, "declared-planned-spend"
    if budget_basis == "daily":
        delivering = sum(
            (budget for grain, budget in grain_budget.items() if grain_spend.get(grain, Decimal("0")) > 0),
            Decimal("0"),
        )
        return (delivering if delivering > 0 else None), "delivering-daily-budget"
    if budget_basis == "lifetime":
        per_campaign: dict[str, Decimal] = {}
        for (campaign_id, _), budget in grain_budget.items():
            per_campaign[campaign_id] = max(per_campaign.get(campaign_id, Decimal("0")), budget)
        total = sum(per_campaign.values(), Decimal("0"))
        return (total if total > 0 else None), "declared-lifetime-budget"
    return None, "undeclared"


def _facts_rows(facts: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    try:
        validate_contract("performance-facts", facts)
    except ContractError as exc:
        raise PerformanceError(str(exc)) from exc
    return facts["rows"]


def score_performance(
    facts: Mapping[str, Any],
    targets: Mapping[str, Any] | None = None,
) -> PerformanceScorecard:
    """Score delivered performance against operator-declared targets.

    ``facts`` is a ``performance-facts`` payload at the documented additive
    ``(date, campaign_id, creative_id)`` grain. ``targets`` supplies the
    operator's own goals; a target that is absent leaves its component
    ``unknown`` and lowers input coverage rather than defaulting to a
    fabricated threshold. Coverage below 60% withholds the score entirely.
    """

    rows = _facts_rows(facts)
    currency = str(facts["currency"])
    resolved_targets = _validated_targets(targets, currency)
    target_cpa = resolved_targets["target_cpa"]
    planned_spend = resolved_targets["planned_spend"]
    budget_basis = resolved_targets["budget_basis"]

    total_spend = Decimal("0")
    total_conversions = Decimal("0")
    campaign_spend: dict[str, Decimal] = {}
    campaign_conversions: dict[str, Decimal] = {}
    campaign_identity: dict[str, tuple[str, str]] = {}
    daily_spend: dict[str, Decimal] = {}
    grain_spend: dict[tuple[str, str], Decimal] = {}
    grain_budget: dict[tuple[str, str], Decimal] = {}

    for index, row in enumerate(rows):
        campaign_id = str(row["campaign_id"])
        spend = _decimal(row["spend"], f"rows[{index}].spend")
        conversions = _decimal(row["conversions"], f"rows[{index}].conversions")
        row_date = str(row["date"])

        total_spend += spend
        total_conversions += conversions
        campaign_spend[campaign_id] = campaign_spend.get(campaign_id, Decimal("0")) + spend
        campaign_conversions[campaign_id] = campaign_conversions.get(campaign_id, Decimal("0")) + conversions
        campaign_identity.setdefault(campaign_id, (str(row["campaign_name"]), str(row["campaign_status"])))
        daily_spend[row_date] = daily_spend.get(row_date, Decimal("0")) + spend

        # A budget repeats across every creative row for the same campaign-day,
        # so it is recorded once per campaign-day grain rather than summed.
        grain = (campaign_id, row_date)
        grain_spend[grain] = grain_spend.get(grain, Decimal("0")) + spend
        grain_budget[grain] = _decimal(row["budget"], f"rows[{index}].budget")

    campaign_budget = _campaign_budget(grain_budget, budget_basis)
    available_budget = sum(campaign_budget.values(), Decimal("0"))
    pacing_reference, pacing_basis = _pacing_reference(
        planned_spend, budget_basis, grain_spend, grain_budget
    )

    missing_inputs: list[str] = []
    caveats: list[str] = []
    components: list[ComponentScore] = []

    delivered = total_spend > 0

    # Efficiency: actual cost per conversion against the declared target.
    if target_cpa is None:
        missing_inputs.append("targets.target_cpa")
        components.append(
            ComponentScore(
                component="efficiency",
                weight=_rounded(COMPONENT_WEIGHTS["efficiency"]),
                score=None,
                status="unknown",
                basis="operator-declared target CPA versus delivered cost per conversion",
                observation="No target CPA was declared, so efficiency cannot be graded.",
            )
        )
    elif not delivered:
        components.append(
            ComponentScore(
                component="efficiency",
                weight=_rounded(COMPONENT_WEIGHTS["efficiency"]),
                score=None,
                status="not_applicable",
                basis="operator-declared target CPA versus delivered cost per conversion",
                observation="The window recorded no spend, so no cost efficiency exists to grade.",
                target_value=_rounded(target_cpa),
            )
        )
    elif total_conversions == 0:
        components.append(
            ComponentScore(
                component="efficiency",
                weight=_rounded(COMPONENT_WEIGHTS["efficiency"]),
                score=0.0,
                status="scored",
                basis="operator-declared target CPA versus delivered cost per conversion",
                observation=(
                    "Spend was delivered with zero recorded conversions, so the declared "
                    "target CPA was not met at any cost level."
                ),
                measured_value=None,
                target_value=_rounded(target_cpa),
            )
        )
    else:
        actual_cpa = total_spend / total_conversions
        ratio = actual_cpa / target_cpa
        score = _clamped_percent((EFFICIENCY_ZERO_RATIO - ratio) / (EFFICIENCY_ZERO_RATIO - Decimal("1")) * Decimal("100"))
        components.append(
            ComponentScore(
                component="efficiency",
                weight=_rounded(COMPONENT_WEIGHTS["efficiency"]),
                score=_rounded(score),
                status="scored",
                basis="operator-declared target CPA versus delivered cost per conversion",
                observation=(
                    f"Delivered CPA is {_rounded(actual_cpa)} {currency} against a declared "
                    f"target of {_rounded(target_cpa)} {currency} (ratio {_rounded(ratio)})."
                ),
                measured_value=_rounded(actual_cpa),
                target_value=_rounded(target_cpa),
            )
        )

    # Waste concentration: spend share on campaigns with no recorded conversion.
    if not delivered:
        components.append(
            ComponentScore(
                component="waste_concentration",
                weight=_rounded(COMPONENT_WEIGHTS["waste_concentration"]),
                score=None,
                status="not_applicable",
                basis="share of delivered spend on campaigns with zero recorded conversions",
                observation="The window recorded no spend, so no allocation exists to grade.",
            )
        )
    else:
        zero_conversion_spend = sum(
            (spend for campaign_id, spend in campaign_spend.items() if campaign_conversions.get(campaign_id, Decimal("0")) == 0),
            Decimal("0"),
        )
        waste_share = zero_conversion_spend / total_spend
        score = _clamped_percent((Decimal("1") - waste_share) * Decimal("100"))
        components.append(
            ComponentScore(
                component="waste_concentration",
                weight=_rounded(COMPONENT_WEIGHTS["waste_concentration"]),
                score=_rounded(score),
                status="scored",
                basis="share of delivered spend on campaigns with zero recorded conversions",
                observation=(
                    f"{_rounded(waste_share * Decimal('100'))}% of spend "
                    f"({_rounded(zero_conversion_spend)} {currency}) sits on campaigns with no "
                    "recorded conversion in this window."
                ),
                measured_value=_rounded(waste_share * Decimal("100")),
            )
        )
        if zero_conversion_spend > 0:
            caveats.append(
                "Zero-conversion spend can reflect conversion lag or an unattributed path, "
                "not only waste. Confirm the conversion window before reallocating."
            )

    # Pacing: delivered spend against the budget the account actually made available.
    if pacing_reference is None:
        missing_inputs.append("targets.planned_spend or targets.budget_basis")
        components.append(
            ComponentScore(
                component="pacing",
                weight=_rounded(COMPONENT_WEIGHTS["pacing"]),
                score=None,
                status="unknown",
                basis="delivered spend versus available budget over the same window",
                observation=(
                    "No spend obligation could be resolved. Declare targets.planned_spend, or "
                    "declare targets.budget_basis so the export's budget column can be read as "
                    "a daily or lifetime figure."
                ),
            )
        )
    else:
        utilization = total_spend / pacing_reference
        deviation = abs(Decimal("1") - utilization)
        score = _clamped_percent((Decimal("1") - deviation / PACING_ZERO_DEVIATION) * Decimal("100"))
        components.append(
            ComponentScore(
                component="pacing",
                weight=_rounded(COMPONENT_WEIGHTS["pacing"]),
                score=_rounded(score),
                status="scored",
                basis=f"delivered spend versus available budget over the same window ({pacing_basis})",
                observation=(
                    f"Spend reached {_rounded(utilization * Decimal('100'))}% of the "
                    f"{_rounded(pacing_reference)} {currency} available over this window."
                ),
                measured_value=_rounded(utilization * Decimal("100")),
                target_value=_rounded(pacing_reference),
            )
        )

    scored = [component for component in components if component.status == "scored"]
    applicable_weight = sum(
        (COMPONENT_WEIGHTS[component.component] for component in components if component.status != "not_applicable"),
        Decimal("0"),
    )
    scored_weight = sum((COMPONENT_WEIGHTS[component.component] for component in scored), Decimal("0"))
    coverage = (
        Decimal("0") if applicable_weight == 0 else scored_weight / applicable_weight * Decimal("100")
    )
    status = _coverage_status(coverage) if applicable_weight > 0 else "insufficient_evidence"

    score_value: float | None = None
    grade: str | None = None
    if status != "insufficient_evidence" and scored_weight > 0:
        weighted = sum(
            (_decimal(component.score, component.component) * COMPONENT_WEIGHTS[component.component] for component in scored),
            Decimal("0"),
        )
        aggregate = weighted / scored_weight
        score_value = _rounded(aggregate)
        grade = _grade(aggregate)

    campaigns = tuple(
        CampaignPerformance(
            campaign_id=campaign_id,
            name=campaign_identity[campaign_id][0],
            status=campaign_identity[campaign_id][1],
            spend=_rounded(spend),
            conversions=_rounded(campaign_conversions.get(campaign_id, Decimal("0"))),
            cpa=(
                None
                if campaign_conversions.get(campaign_id, Decimal("0")) == 0
                else _rounded(spend / campaign_conversions[campaign_id])
            ),
            spend_share=_rounded(Decimal("0") if total_spend == 0 else spend / total_spend * Decimal("100")),
            budget_total=None if campaign_id not in campaign_budget else _rounded(campaign_budget[campaign_id]),
            budget_utilization=(
                None
                if campaign_budget.get(campaign_id, Decimal("0")) == 0
                else _rounded(spend / campaign_budget[campaign_id] * Decimal("100"))
            ),
            target_cpa_ratio=(
                None
                if target_cpa is None or campaign_conversions.get(campaign_id, Decimal("0")) == 0
                else _rounded(spend / campaign_conversions[campaign_id] / target_cpa)
            ),
        )
        for campaign_id, spend in sorted(campaign_spend.items())
    )

    opportunities = tuple(
        {
            "kind": "budget-constrained-efficient-campaign",
            "campaign_id": campaign.campaign_id,
            "observation": (
                f"Campaign reached {campaign.budget_utilization}% budget utilization at a CPA "
                f"of {campaign.cpa} {currency}, at or below the declared target."
            ),
            "requires": "eligibility, margin, and learning-phase review before any budget change",
        }
        for campaign in campaigns
        if campaign.budget_utilization is not None
        and campaign.budget_utilization >= 95.0
        and campaign.target_cpa_ratio is not None
        and campaign.target_cpa_ratio <= 1.0
    )

    if len(daily_spend) < 2:
        caveats.append(
            "The window contains fewer than two distinct days, so no delivery trend is observable."
        )
    if total_conversions > 0 and total_conversions < 30:
        caveats.append(
            f"Only {_rounded(total_conversions)} conversions were recorded. Treat campaign-level "
            "CPA comparisons as directional, not decisive."
        )

    return PerformanceScorecard(
        schema_version="1.0.0",
        account=dict(facts["account"]),
        window=dict(facts["window"]),
        currency=currency,
        performance_score=score_value,
        grade=grade,
        status=status,
        input_coverage=_rounded(coverage),
        scoring_convention=SCORING_CONVENTION,
        grade_scale=GRADE_SCALE,
        grading_basis="operator-declared-targets-and-own-export",
        totals={
            "spend": _rounded(total_spend),
            "conversions": _rounded(total_conversions),
            "cpa": None if total_conversions == 0 else _rounded(total_spend / total_conversions),
            "available_budget": None if available_budget == 0 else _rounded(available_budget),
            "pacing_basis": pacing_basis,
            "campaign_count": len(campaign_spend),
            "day_count": len(daily_spend),
        },
        components=tuple(components),
        campaigns=campaigns,
        daily_spend=tuple(
            {"date": day, "spend": _rounded(spend)} for day, spend in sorted(daily_spend.items())
        ),
        opportunities=opportunities,
        missing_inputs=tuple(missing_inputs),
        caveats=tuple(caveats),
    )
