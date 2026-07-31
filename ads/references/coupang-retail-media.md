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

When an operator supplies a Coupang report, return all of the following.

1. **Placement budget split.** Current versus recommended share for
   `검색`, `비검색`, and `오디언스 플러스`, with each placement's own conversion
   rate, cost per order, and ROAS for every period supplied. Recommend the split
   from observed cost per order, not from a fixed ratio.
2. **Priority keywords with a recommended bid.** Rank by spend. For each, give
   pooled clicks, pooled orders, conversion rate, current CPC, recommended bid,
   and the adjustment.
3. **Non-search placement bid.** The same calculation applied to `비검색` and
   `오디언스 플러스`, which have no keyword-level control.
4. **Reduce or pause candidates**, separated from items held back for
   insufficient sample.

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
