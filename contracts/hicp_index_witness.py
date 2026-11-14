# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""HicpIndexWitness: an inflation factor that validators can recompute.

The contract turns two published values of the official Harmonised Index of
Consumer Prices into one integer multiplier, for example "prices at 2025-12 were
1.019969 times the level of 2024-12". The multiplier is stored together with the
verdict that code derives from a limit supplied by the caller.

Nothing in the record comes from a language model. The only non-deterministic
step is reading the public dataset, and the validator re-runs the whole
computation, so every stored field is independently derived by every node.
"""

from genlayer import *
import json

ENDPOINT = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_midx"
DATASET = "prc_hicp_midx"
UNIT = "I15"
COICOP = "CP00"
GEOS = ("DE", "AT", "FR", "NL", "IT", "ES", "PL", "BE", "EU27_2020")
FIRST_YEAR = 1996
MAX_LIMIT_BP = 10000


def _is_month(value: str) -> bool:
    if len(value) != 7 or value[4] != "-":
        return False
    if not (value[0:4] + value[5:7]).isdigit():
        return False
    year = int(value[0:4])
    month = int(value[5:7])
    return year >= FIRST_YEAR and month >= 1 and month <= 12


def _month_ordinal(value: str) -> int:
    return int(value[0:4]) * 12 + int(value[5:7]) - 1


def _milli(value) -> int:
    """Turn an index level such as 132.8 into the integer 132800."""
    text = "%.3f" % value
    whole = text[0:-4]
    fraction = text[-3:]
    if whole == "-0":
        whole = "0"
    return int(whole + fraction)


def _build_url(geo: str) -> str:
    return (
        ENDPOINT
        + "?format=JSON&lang=EN&geo="
        + geo
        + "&coicop="
        + COICOP
        + "&unit="
        + UNIT
    )


def _extract(payload: dict, geo: str, base_month: str, target_month: str) -> list:
    """Return the two index levels, using the JSON-stat flat index layout."""
    dims = payload.get("id")
    sizes = payload.get("size")
    if not isinstance(dims, list) or not isinstance(sizes, list):
        raise gl.vm.UserError("dataset has no dimensions")
    if len(dims) == 0 or len(dims) != len(sizes):
        raise gl.vm.UserError("dataset dimensions are inconsistent")

    strides = []
    for i in range(len(sizes)):
        step = 1
        for j in range(i + 1, len(sizes)):
            step = step * sizes[j]
        strides.append(step)

    wanted = {"freq": "M", "unit": UNIT, "coicop": COICOP, "geo": geo}
    fixed = {}
    time_strides = 0
    time_index = {}
    seen_time = False

    for i in range(len(dims)):
        dim = dims[i]
        category = payload.get("dimension", {}).get(dim, {}).get("category", {}).get("index")
        if not isinstance(category, dict):
            raise gl.vm.UserError("dataset has an unreadable dimension: " + str(dim))
        if dim == "time":
            seen_time = True
            time_strides = strides[i]
            time_index = category
        elif dim in wanted:
            code = wanted[dim]
            if code not in category:
                raise gl.vm.UserError("dataset does not contain " + code)
            fixed[i] = category[code]

    if not seen_time:
        raise gl.vm.UserError("dataset has no time dimension")

    values = payload.get("value")
    if not isinstance(values, dict):
        raise gl.vm.UserError("dataset has no values")

    levels = []
    for month in (base_month, target_month):
        if month not in time_index:
            raise gl.vm.UserError("month " + month + " is not published in this dataset")
        flat = time_index[month] * time_strides
        for i in fixed:
            flat = flat + fixed[i] * strides[i]
        level = values.get(str(flat))
        if level is None:
            raise gl.vm.UserError("month " + month + " has no published value")
        if not isinstance(level, (int, float)):
            raise gl.vm.UserError("month " + month + " is not a number")
        levels.append(level)
    return levels


class HicpIndexWitness(gl.Contract):
    next_id: u256
    records: TreeMap[str, str]
    order: DynArray[str]

    def __init__(self):
        self.next_id = u256(1)

    @gl.public.write
    def record(self, geo: str, base_month: str, target_month: str, limit_bp: u256) -> str:
        if geo not in GEOS:
            raise gl.vm.UserError("unsupported country code")
        if not _is_month(base_month):
            raise gl.vm.UserError("base_month must look like 2024-12")
        if not _is_month(target_month):
            raise gl.vm.UserError("target_month must look like 2025-12")
        if _month_ordinal(target_month) <= _month_ordinal(base_month):
            raise gl.vm.UserError("target_month must be later than base_month")
        limit = int(limit_bp)
        if limit < 0 or limit > MAX_LIMIT_BP:
            raise gl.vm.UserError("limit_bp must be between 0 and 10000")

        record_key = geo + "|" + base_month + "|" + target_month + "|" + str(limit)
        if self.records.get(record_key, "") != "":
            raise gl.vm.UserError("this country, month pair and limit are already recorded")

        url = _build_url(geo)

        def leader_fn() -> str:
            response = gl.nondet.web.get(url)
            if response.status != 200:
                raise gl.vm.UserError("dataset host answered with status " + str(response.status))
            try:
                payload = json.loads(response.body.decode("utf-8", "replace"))
            except Exception:
                raise gl.vm.UserError("dataset answer is not valid JSON")
            if not isinstance(payload, dict):
                raise gl.vm.UserError("dataset answer is not a JSON object")

            levels = _extract(payload, geo, base_month, target_month)
            base_milli = _milli(levels[0])
            target_milli = _milli(levels[1])
            if base_milli <= 0:
                raise gl.vm.UserError("base index level is not positive")

            factor_ppm = target_milli * 1000000 // base_milli
            rate_bp = (factor_ppm - 1000000) // 100
            if rate_bp > limit:
                verdict = "ABOVE_LIMIT"
            else:
                verdict = "WITHIN_LIMIT"

            return json.dumps(
                {
                    "geo": geo,
                    "base_month": base_month,
                    "target_month": target_month,
                    "base_index_milli": base_milli,
                    "target_index_milli": target_milli,
                    "factor_ppm": factor_ppm,
                    "rate_bp": rate_bp,
                    "limit_bp": limit,
                    "verdict": verdict,
                    "source": DATASET + ":" + UNIT + ":" + COICOP,
                },
                sort_keys=True,
            )

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

        result = json.loads(gl.vm.run_nondet(leader_fn, validator_fn))

        record_id = str(int(self.next_id))
        record = {
            "id": record_id,
            "record_key": record_key,
            "assessment": result,
        }
        self.records[record_key] = json.dumps(record, sort_keys=True)
        self.order.append(record_key)
        self.next_id = u256(int(self.next_id) + 1)
        return record_id

    @gl.public.view
    def get_record(self, record_id: str) -> str:
        index = record_id.strip()
        if not index.isdigit():
            return "{}"
        position = int(index) - 1
        if position < 0 or position >= len(self.order):
            return "{}"
        return self.records.get(self.order[position], "{}")

    @gl.public.view
    def count(self) -> u32:
        return len(self.order)

    @gl.public.view
    def get_key(self, position: int) -> str:
        if position < 0 or position >= len(self.order):
            return ""
        return self.order[position]
