import json

CONTRACT = "contracts/hicp_index_witness.py"
URL_PATTERN = r".*prc_hicp_midx.*"

LEVELS = {"2024-12": 130.2, "2025-06": 131.9, "2025-12": 132.8}


def dataset(levels=None, geo="DE"):
    """A minimal Eurostat JSON-stat payload with the dimensions the contract reads."""
    levels = LEVELS if levels is None else levels
    months = list(levels.keys())
    return {
        "id": ["freq", "unit", "coicop", "geo", "time"],
        "size": [1, 1, 1, 1, len(months)],
        "dimension": {
            "freq": {"category": {"index": {"M": 0}}},
            "unit": {"category": {"index": {"I15": 0}}},
            "coicop": {"category": {"index": {"CP00": 0}}},
            "geo": {"category": {"index": {geo: 0}}},
            "time": {"category": {"index": {month: position for position, month in enumerate(months)}}},
        },
        "value": {str(position): levels[month] for position, month in enumerate(months)},
    }


def install(direct_vm, payload, status=200):
    direct_vm.clear_mocks()
    direct_vm.mock_web(
        URL_PATTERN,
        {"method": "GET", "status": status, "body": json.dumps(payload)},
    )


def test_matches_a_hand_computed_factor(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, dataset())
    record_id = contract.record("DE", "2024-12", "2025-12", 300)
    assert record_id == "1"

    record = json.loads(contract.get_record("1"))
    assessment = record["assessment"]
    assert assessment["base_index_milli"] == 130200
    assert assessment["target_index_milli"] == 132800
    assert assessment["factor_ppm"] == 1019969
    assert assessment["rate_bp"] == 199
    assert assessment["verdict"] == "WITHIN_LIMIT"
    assert assessment["source"] == "prc_hicp_midx:I15:CP00"


def test_limit_flips_the_verdict(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, dataset())
    contract.record("DE", "2024-12", "2025-12", 100)
    assessment = json.loads(contract.get_record("1"))["assessment"]
    assert assessment["rate_bp"] == 199
    assert assessment["verdict"] == "ABOVE_LIMIT"
    assert assessment["limit_bp"] == 100


def test_long_range_uses_endpoints_only(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, dataset({"\u0031\u0039\u0039\u0036-01": 75.1, "2025-12": 132.8}))
    contract.record("DE", "1996-01", "2025-12", 10000)
    assessment = json.loads(contract.get_record("1"))["assessment"]
    assert assessment["factor_ppm"] == 1768308
    assert assessment["rate_bp"] == 7683


def test_same_pair_and_limit_is_rejected(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, dataset())
    contract.record("DE", "2024-12", "2025-12", 300)
    with direct_vm.expect_revert("already recorded"):
        contract.record("DE", "2024-12", "2025-12", 300)


def test_same_pair_can_be_read_against_another_limit(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, dataset())
    contract.record("DE", "2024-12", "2025-12", 300)
    contract.record("DE", "2024-12", "2025-12", 100)

    first = json.loads(contract.get_record("1"))["assessment"]
    second = json.loads(contract.get_record("2"))["assessment"]
    assert first["rate_bp"] == second["rate_bp"] == 199
    assert first["verdict"] == "WITHIN_LIMIT"
    assert second["verdict"] == "ABOVE_LIMIT"
    assert contract.count() == 2


def test_input_validation(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, dataset())

    with direct_vm.expect_revert("unsupported country"):
        contract.record("XX", "2024-12", "2025-12", 300)
    with direct_vm.expect_revert("base_month"):
        contract.record("DE", "2024-13", "2025-12", 300)
    with direct_vm.expect_revert("target_month"):
        contract.record("DE", "2024-12", "25-12", 300)
    with direct_vm.expect_revert("later than base_month"):
        contract.record("DE", "2025-12", "2024-12", 300)
    with direct_vm.expect_revert("later than base_month"):
        contract.record("DE", "2025-12", "2025-12", 300)
    with direct_vm.expect_revert("limit_bp"):
        contract.record("DE", "2024-12", "2025-12", 10001)


def test_unpublished_month_reverts(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, dataset())
    with direct_vm.expect_revert("not published"):
        contract.record("DE", "2024-12", "2026-06", 300)
    assert contract.count() == 0


def test_host_error_reverts(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, dataset(), status=503)
    with direct_vm.expect_revert("status 503"):
        contract.record("DE", "2024-12", "2025-12", 300)
    assert contract.count() == 0


def test_missing_geo_dimension_reverts(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    payload = dataset()
    payload["dimension"]["geo"]["category"]["index"] = {"AT": 0}
    install(direct_vm, payload)
    with direct_vm.expect_revert("does not contain DE"):
        contract.record("DE", "2024-12", "2025-12", 300)


def test_missing_value_reverts(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    payload = dataset()
    payload["value"].pop("2")
    install(direct_vm, payload)
    with direct_vm.expect_revert("no published value"):
        contract.record("DE", "2024-12", "2025-12", 300)


def test_index_and_count(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, dataset())
    contract.record("DE", "2024-12", "2025-12", 300)
    contract.record("DE", "2025-06", "2025-12", 300)

    assert contract.count() == 2
    assert contract.get_key(0) == "DE|2024-12|2025-12|300"
    assert contract.get_key(1) == "DE|2025-06|2025-12|300"
    assert contract.get_key(2) == ""
    assert contract.get_record("9") == "{}"
    assert contract.get_record("abc") == "{}"


def test_validator_accepts_a_genuine_leader(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, dataset())
    contract.record("DE", "2024-12", "2025-12", 300)
    assert direct_vm.run_validator() is True


def test_validator_rejects_a_tampered_rate(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, dataset())
    contract.record("DE", "2024-12", "2025-12", 300)
    genuine = json.loads(contract.get_record("1"))["assessment"]

    forged = dict(genuine)
    forged["rate_bp"] = 50
    forged["verdict"] = "WITHIN_LIMIT"
    assert direct_vm.run_validator(leader_result=json.dumps(forged, sort_keys=True)) is False


def test_validator_rejects_a_flipped_verdict(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, dataset())
    contract.record("DE", "2024-12", "2025-12", 300)
    genuine = json.loads(contract.get_record("1"))["assessment"]

    forged = dict(genuine)
    forged["verdict"] = "ABOVE_LIMIT"
    assert direct_vm.run_validator(leader_result=json.dumps(forged, sort_keys=True)) is False


def test_validator_rejects_a_different_source(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, dataset())
    contract.record("DE", "2024-12", "2025-12", 300)
    genuine = json.loads(contract.get_record("1"))["assessment"]

    forged = dict(genuine)
    forged["source"] = "some-other-dataset"
    assert direct_vm.run_validator(leader_result=json.dumps(forged, sort_keys=True)) is False


def test_validator_rejects_leader_error(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, dataset())
    contract.record("DE", "2024-12", "2025-12", 300)
    assert direct_vm.run_validator(leader_error=RuntimeError("host unavailable")) is False
