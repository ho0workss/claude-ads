# Coupang retail media reporting

Coupang is outside the twelve-platform product contract. Everything here is
derived from an operator's own exported report and their own declared target.
Nothing in this file asserts a Coupang placement, auction, ranking, or
measurement behavior, and no threshold is borrowed from another platform. Treat
it as an output contract for reading an operator-supplied export, not as a
platform capability claim.

## Export shape

A Coupang report mixes two grains in one sheet. Aggregating without separating
them produces wrong denominators.

- Keyword rows: `노출 영역 = 검색 영역`, one row per keyword per day.
- Placement rows: `비검색 영역` and `오디언스 플러스(외부 채널)`, already
  aggregated, with no keyword.
- Occasional orphan rows carry attributed revenue with zero spend.

Columns present: `날짜`, `캠페인 이름`, `노출 영역`, `키워드`, `노출수`,
`클릭수`, `광고비(원)`, `참여수`, and both `(1일)` and `(14일)` attribution for
`총 주문수`, `총 전환 매출액`, and `총 판매 수량`. There is no budget column, so
pacing needs a declared `planned_spend`. Some exports have no `날짜` column at
all, which removes every trend and pacing conclusion.

## Masked order counts

A campaign billed per engagement (`과금 방식 = 참여당 과금`) may report
`총 주문수` as `-` on every row while still reporting revenue. Its clicks are
real but its orders are not.

Including such a campaign's clicks in a conversion-rate denominator biases the
rate downward and produces bid recommendations that are too low. Compute every
conversion rate and bid from campaigns whose orders are reported, and state
which campaigns were excluded and why.

Where only revenue is available, derive an order estimate as
`revenue / unit price` only when the export shows a single repeating unit price,
and label it an estimate.

## Attribution windows

Report `1일` and `14일` side by side. Never sum them.

Check maturity before comparing periods: divide 14-day revenue by 1-day revenue.
A period whose ratio is near 1.0 while comparable periods sit higher has not
finished attributing, and its performance is understated. Pull the export at
least fourteen days after the window closes, or state the adjustment explicitly.

## Required output

Lead with the keyword decisions. An operator acts on which keywords to add and
which to exclude, so those come first and everything else supports them.

1. **Keywords to add.** Keywords with a conversion history whose recent spend
   has stopped or collapsed, so bidding must be restored for them to serve
   again. Give pooled clicks, pooled orders, conversion rate, and the
   recommended bid.
2. **Keywords to exclude.** Register as negatives or stop bidding. Two grounds
   only: enough clicks with zero orders, or a cost per order above twice the
   declared target with enough clicks to judge. State which ground applies.
3. **Bid increases and decreases** on keywords that stay active.
4. **Held for insufficient sample**, listed separately so a thin keyword is
   never mistaken for a proven loser.
5. **Long-tail block.** Auto-expanded keywords below the click floor are too
   small to judge individually and too numerous to exclude one at a time.
   Report their count and combined spend, and recommend one bid ceiling for the
   block rather than a list of negatives.
6. **Placement budget split.** Current versus recommended share for `검색`,
   `비검색`, and `오디언스 플러스`, with each placement's conversion rate, cost
   per order, and ROAS per period. Recommend from observed cost per order, not a
   fixed ratio.
7. **Non-search placement bids** for `비검색` and `오디언스 플러스`, which have
   no keyword-level control.

Round every recommended bid to the nearest 10 KRW. Report bids as whole won
figures, never with decimals.

## Bid formula

```text
recommended_bid = declared_target_cost_per_order x conversion_rate
```

The target cost per order must be declared by the operator. Never substitute an
industry figure, a competitor figure, or a value inferred from the account's own
history without the operator confirming it as their target.

Pool clicks and orders across every supplied period so the rate is stable, then
compare against the most recent period:

- Recent rate within 25% of the pooled rate: use the pooled rate.
- Recent rate more than 25% below pooled: use the recent rate, and say the
  recommendation is deliberately conservative.
- Fewer than 20 pooled clicks: return no bid. Report the gap instead.

Label confidence by pooled clicks: 100 or more is high, 50 to 99 is medium,
below 50 is low. A bid built on fewer than five orders moves with a single
order; say so next to the number.

A recommendation whose direction contradicts its bucket is a classification
error, not a finding. Sort by the action the number implies: a keyword whose
recommended bid falls below its current CPC belongs in the decrease list even if
its spend collapsed, and never in the list of keywords to restore.

## Daily budget to base bid

When the operator states a daily budget, also return a base bid per placement —
the campaign-level bid that applies where no keyword bid overrides it.

```text
base_bid            = declared_target_cost_per_order x placement_conversion_rate
required_clicks     = placement_budget / base_bid
```

Compare `required_clicks` against the highest daily click count that placement
has ever recorded. At or below 80% of it, the budget is spendable. Between 80%
and 120%, say it is tight. Above 120%, the budget cannot be spent at that bid:
report the bid the budget would demand and say plainly that paying it breaks the
declared target, so the operator chooses between the budget and the target.

Two limits belong in the output every time, because omitting either makes the
recommendation look stronger than it is.

- The observed click ceiling was measured at the CPC actually paid then. A base
  bid below that CPC buys less inventory, so a placement whose recommended bid
  cuts its historical CPC will under-deliver its allocation. Say so instead of
  reporting the capacity check as a clean pass.
- Expected orders is not a forecast. Because each base bid is derived from the
  target, `budget / base_bid x conversion_rate` reduces to
  `budget / target_cost_per_order` for every budget and every split. Present it
  as the identity it is. A real forecast needs the conversion rate to hold at
  the new bid, which is exactly what the change is testing.

## Guardrails

Never recommend pausing a keyword on zero orders alone. Separate "no orders with
enough clicks to judge" from "not enough clicks yet". Below roughly 50 clicks a
zero-order keyword is unresolved, not proven dead.

A large recommended increase on a high-rate keyword usually rests on a small
order count. Cap a single-step bid increase and re-measure rather than applying
the full computed figure at once.

Rising cost per order with a stable conversion rate is an auction or bid
problem. A stable conversion rate on search with a falling rate off search is a
placement problem, not a product-page problem. A conversion rate falling on both
search and non-search at once points past the ad account to price, stock,
reviews, delivery, or the listing itself; say that the ad data cannot identify
which, and name what the operator must check.

Placement recommendations shift budget between placements the operator already
runs. Applying any change stays subject to the mutation gate in `ads/SKILL.md`.
