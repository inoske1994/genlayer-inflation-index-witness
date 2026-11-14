# InflationIndexWitness

A GenLayer Intelligent Contract that turns two published values of the official German consumer price index into one integer multiplier, and stores the result with the source it came from.

## What it actually records

You pass a country, a base month, a target month, and a limit in basis points. The contract fetches the Harmonised Index of Consumer Prices from Eurostat, reads the two index levels, and stores a record like this:

```
DE 2024-12 -> 2025-12
base_index_milli   130200      (index 130.2)
target_index_milli 132800      (index 132.8)
factor_ppm         1019969     (prices became 1.019969 times the base level)
rate_bp            199         (1.99 percent)
limit_bp           200
verdict            WITHIN_LIMIT
source             prc_hicp_midx:I15:CP00
```

Two outcomes exist, and both are derived by code from the stored integers:

| Verdict | Rule |
| --- | --- |
| `WITHIN_LIMIT` | `rate_bp <= limit_bp` |
| `ABOVE_LIMIT` | `rate_bp > limit_bp` |

The caller supplies the policy, the contract supplies the verified fact. Nothing in the record depends on a model, a website price table, or an off-chain operator.

## Why a contract is useful here

Contracts that index payments to inflation need the index value, and today they get it from an oracle or from a human typing a number into a dashboard. Both are single points that can be wrong, stale, or unavailable, and neither leaves a trail a counterparty can check.

The interesting property of a published statistical series is that it is public, free, and identical for everyone. Once two parties agree on the dataset, the country, and the two months, they have already agreed on the number. What they cannot do cheaply is agree on the fetching. That is the part GenLayer solves: several validators fetch independently and must reach the same record before anything is written.

Uses that outlive a demo: escrowed rent or salary indexed to published inflation, sustainability-linked or index-linked payouts, treasury reporting that can be audited against the same source later, and any contract that needs an official European series without trusting a single server.

## Consensus design

The output is a function of public data, so the contract does not use a language model at all. Adding one would only add a source of disagreement.

The write method runs inside `gl.vm.run_nondet`. The leader fetches the dataset, parses it, computes every field, and returns a JSON object. The validator does not compare opinions. It runs the same function itself, from its own fetch, and accepts only if the two objects are equal:

```python
def validator_fn(leaders_res) -> bool:
    if not isinstance(leaders_res, gl.vm.Return):
        return False
    try:
        proposed = json.loads(leaders_res.calldata)
        checked = json.loads(leader_fn())
    except Exception:
        return False
    if not isinstance(proposed, dict):
        return False
    return proposed == checked
```

That one line, `proposed == checked`, is the whole consensus rule, and it covers every field: both index levels, the multiplier, the rate, the verdict, the source string, and the limit. There is no field that only the leader decides, which was the failure mode in an earlier project of mine and the reason it was rejected.

State changes only after agreement. If the host fails, the payload is malformed, a month is not published, or validators disagree, the transaction reverts and nothing is stored. A record is never half written, and a missing month is never recorded as zero inflation.

Direct mode tests cover the rejections directly: a tampered rate, a flipped verdict, a changed source string, and a failed leader all return `False` from the validator.

Limits of this design are in `docs/CONSENSUS.md`.

## Live runs on Studionet

Chain `61999`, contract [`0xbd7a5D067E7D22B4Bb80FB6b201EA14C9aCaC6F1`](https://explorer-studio.genlayer.com/address/0xbd7a5D067E7D22B4Bb80FB6b201EA14C9aCaC6F1), deployer `0x264465Ee4bCe6E6ba6be6c976dE8CfccA2148B09`. Every call used 0 GEN and ran with execution mode `Normal (Full Consensus)`.

| Step | Transaction | Consensus | Result |
| --- | --- | --- | --- |
| Deploy | [`0x464632a9...f0d47f03`](https://explorer-studio.genlayer.com/tx/0x464632a9e0bb8cf7a2f33980b3f37375f2bc3a7808e292d090c21287f0d47f03) | Finalized, Accepted, rotation 0 | contract created |
| `record` DE 2024-12 to 2025-12, limit 200 | [`0x7c7502b7...b6613536`](https://explorer-studio.genlayer.com/tx/0x7c7502b7bbb6c99c9961e228a306de609443d9ce636d1315a563060db6613536) | Finalized, rotation 0, all validators agree | record `1`, `rate_bp` 199, `WITHIN_LIMIT` |
| `record` DE 2025-01 to 2025-12, limit 200 | [`0x93e489b2...78b0ab1a`](https://explorer-studio.genlayer.com/tx/0x93e489b265330ebe9c55929dc3a366cd83f3d66c68fd0997730f135778b0ab1a) | Finalized, rotation 0, all validators agree | record `2`, `rate_bp` 223, `ABOVE_LIMIT` |
| `record` DE 1996-01 to 2025-12, limit 5000 | [`0xa60e8328...22dabdef`](https://explorer-studio.genlayer.com/tx/0xa60e8328c27c0965a29103785e85603fff82dc401e63163ea7d957f822dabdef) | Finalized, rotation 0, all validators agree | record `3`, `rate_bp` 7683, `ABOVE_LIMIT` |

Reading `get_record("1")` afterwards returns the stored object:

```json
{
  "assessment": {
    "base_index_milli": 130200,
    "base_month": "2024-12",
    "factor_ppm": 1019969,
    "geo": "DE",
    "limit_bp": 200,
    "rate_bp": 199,
    "source": "prc_hicp_midx:I15:CP00",
    "target_index_milli": 132800,
    "target_month": "2025-12",
    "verdict": "WITHIN_LIMIT"
  },
  "id": "1",
  "record_key": "DE|2024-12|2025-12|200"
}
```

Before deploying, the same three cases were computed off chain directly against the Eurostat endpoint by a separate script, and the numbers matched the on-chain records exactly. The two examples with the same target month and different base months show that the multiplier is not a property of the dataset but of the pair: 1.99 percent over twelve months, 2.23 percent over eleven.

## Deploy and call

1. In [Studio](https://studio.genlayer.com), paste `contracts/hicp_index_witness.py` and deploy with `Normal (Full Consensus)`.
2. Call `record` with `DE`, `2024-12`, `2025-12`, `200`. It returns `1` after roughly 20 to 60 seconds.
3. Read `get_record("1")`, `count()`, or `get_key(0)`.

Supported country codes: `DE`, `AT`, `FR`, `NL`, `IT`, `ES`, `PL`, `BE`, `EU27_2020`. Index unit is `I15`, meaning 2015 = 100, and the item class is `CP00`, meaning all items.

## Tests

```
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest tests/direct -q
```

16 direct mode tests, all passing on the state actually deployed. They cover the arithmetic against hand computed values, the two verdicts from the same data, the duplicate rule, six input validations, five failure paths, the index and count views, and four validator rejections.

Direct mode replaces the web with a mock payload. It proves the contract logic and the validator rule, not that five validators agree. The live table above is the evidence for the network side, and the two kinds of evidence are kept separate on purpose.

## What this does not prove

- It does not prove that the Eurostat series is correct, only that the record matches what Eurostat published at the moment validators fetched it.
- It does not cover a month that Eurostat has not published yet. Such a call reverts, so a record never claims deflation or stability by accident.
- Revisions are not tracked. If Eurostat revises a past month, an old record keeps the old numbers, and a new record with the same key is rejected. The record is a snapshot, not a mirror.
- The rounding is integer and truncated, so `rate_bp` can sit at most 0.01 percentage point away from a full precision calculation.
- It does not decide whether the indexed payment is fair, legal, or what the parties intended.

More detail, including what the consensus rule cannot catch, is in [docs/CONSENSUS.md](docs/CONSENSUS.md).

## Layout

```
contracts/hicp_index_witness.py     the contract, one file, only json imported
tests/direct/                       16 direct mode tests
docs/CONSENSUS.md                   consensus rule, threat table, and its limits
requirements-dev.txt                pinned test toolchain
```

## Data

Dataset `prc_hicp_midx` (HICP monthly index), published by Eurostat, source `ESTAT`, unit `Index, 2015=100`. Eurostat publishes its statistics for reuse with attribution, and the record stores the dataset, unit and item class it used so the same query can be repeated.

MIT licensed.
