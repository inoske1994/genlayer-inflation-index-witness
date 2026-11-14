# Consensus

This document explains what validators check, why the rule is shaped the way it is, and what it does not catch.

## One rule

The write method fetches a public dataset and computes integers from it. The leader does that work and returns a JSON object. The validator runs the same function from its own fetch and compares:

```python
return proposed == checked
```

Equality covers every field of the record: `base_index_milli`, `target_index_milli`, `factor_ppm`, `rate_bp`, `limit_bp`, `verdict`, `source`, `geo`, `base_month`, `target_month`. There is no field produced by the leader alone, and nothing is merged from several nodes. If the objects differ in any way, the validator rejects.

This matters because the alternative shape, where the leader contributes fields that validators do not re-derive, cannot be fixed by a better prompt. Any input that can change the stored result must be derived by every node, or the stored result is not a consensus fact.

## Why there is no model

Every value in the record is arithmetic over numbers that the dataset publishes:

- the two index levels, read from the JSON-stat response,
- the multiplier `target_index_milli * 1000000 // base_index_milli`,
- the rate `(factor_ppm - 1000000) // 100`,
- the verdict, from a comparison with the caller's limit.

A language model would add variance without adding information, and it would create the exact failure the rule above is designed to prevent. The hard part of this contract is not reading the numbers, it is agreeing on the fetch. That is what GenLayer contributes.

## Determinism boundaries

| Value | Deterministic | Under consensus |
| --- | --- | --- |
| HTTP response body | no | yes, through full equality of every derived field |
| index levels | given the body | yes |
| multiplier and rate | yes, integer arithmetic | yes |
| verdict | yes, from the caller's limit | yes |
| source string | yes, constant | yes |
| whether a month exists in the dataset | no | yes, because a missing month reverts the transaction |

Integer arithmetic is used everywhere on purpose. Index levels are converted to thousandths with `"%.3f"` before any division, and the multiplier is truncated to parts per million. Floats never reach stored state, so two nodes running on different machines cannot disagree in the last digit.

## What this rule stops

| Attempted by a leader | Stopped by |
| --- | --- |
| Change one index level | full equality |
| Compute a different multiplier from correct levels | full equality |
| Flip the verdict while keeping the numbers | full equality |
| Point at another dataset or another unit | `source` is part of the comparison |
| Invent a value for a month that is not published | `_extract` raises, transaction reverts |
| Return a stale record from an earlier call | every field is recomputed from a fresh fetch |
| Return malformed JSON | the payload parser rejects, nothing is written |
| Write a partial record when validators disagree | state is written only after `run_nondet` returns |

Four of these are exercised as direct mode tests: a tampered rate, a flipped verdict, a changed source string, and a failed leader.

## What it does not catch

The dataset is trusted in one narrow sense: if Eurostat serves wrong numbers to all validators, the record faithfully preserves wrong numbers. The contract proves agreement on what the source said, not that the source is right. This is the same limitation every web oracle has, and pretending otherwise would be dishonest.

Validators fetch at slightly different moments. For published months this is harmless, because the series is immutable. If Eurostat updates the dataset between two fetches in a way that changes a requested month, validators disagree and the transaction reverts rather than storing a mixture. That is fail safe, but it is not a fix.

Revisions are not tracked. A record written today keeps the numbers Eurostat published today, even if a later revision changes them, and the same key cannot be written twice. The record is a snapshot with a source, not a live mirror.

Index level conversion truncates rather than rounds. `rate_bp` can therefore sit at most 0.01 percentage point away from a full precision calculation. The truncation is deliberate: rounding modes are a classic source of cross platform disagreement, and a stable integer is worth more than a decimal place.

## A note on an earlier design

An earlier contract of mine stored a licence posture and left an uncertainty flag under the leader's control, which meant the stored outcome could differ from what validators had actually checked. It was rejected for exactly that reason. This contract was built with that critique in mind: the validator derives the whole record, so there is nothing left for the leader to decide.
