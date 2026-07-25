# Notes Retrieval Quality Benchmark

Generated: 2026-07-25T14:41:36.530741+00:00

Retrieval quality measured on REAL memory notes (a throwaway copy of the live memory home — the real store is never searched). Queries are derived mechanically from each gold note's own title (first 5-10 words), so absolute Hit@k is inflated by lexical overlap. The unbiased signal is the **recency-on − recency-off delta**: identical queries and indexes in both runs, so the difference isolates the recency bonus (RECENCY_BONUS_MAX=0.05).

Top-10; tier_filter covers all tiers (durable, episodic, reference) so the deliberate default exclusion of `episodic` does not masquerade as a ranking miss.

## Corpus: global (scope=global)

**77** active notes, **77** mechanically-derived queries.

| Run | Queries | Hit@1 | Hit@3 | Hit@5 | MRR |
|---|---|---|---|---|---|
| recency ON | 77 | 84.4% | 84.4% | 84.4% | 0.844 |
| recency OFF | 77 | 84.4% | 84.4% | 84.4% | 0.844 |

**Delta (on − off):** Hit@1 +0.0% · Hit@3 +0.0% · Hit@5 +0.0% · MRR +0.000

**Gold rank changes (on vs off):** 0 up · 0 down · 77 unchanged.

| Tier (recency ON) | Queries | Hit@1 | Hit@3 | Hit@5 | MRR |
|---|---|---|---|---|---|
| durable | 74 | 83.8% | 83.8% | 83.8% | 0.838 |
| episodic | 3 | 100.0% | 100.0% | 100.0% | 1.000 |

## Corpus: project:CATS (scope=project)

**507** active notes, **507** mechanically-derived queries.

| Run | Queries | Hit@1 | Hit@3 | Hit@5 | MRR |
|---|---|---|---|---|---|
| recency ON | 507 | 98.8% | 100.0% | 100.0% | 0.994 |
| recency OFF | 507 | 99.0% | 100.0% | 100.0% | 0.995 |

**Delta (on − off):** Hit@1 -0.2% · Hit@3 +0.0% · Hit@5 +0.0% · MRR -0.001

**Gold rank changes (on vs off):** 0 up · 1 down · 506 unchanged.

| Tier (recency ON) | Queries | Hit@1 | Hit@3 | Hit@5 | MRR |
|---|---|---|---|---|---|
| durable | 430 | 98.8% | 100.0% | 100.0% | 0.994 |
| episodic | 77 | 98.7% | 100.0% | 100.0% | 0.994 |

> Caveat: title-derived queries favour lexical matching; if every gold ranks #1 in both runs the corpus may simply be easy for this query style — treat the delta and rank-change counts as the real signal.
