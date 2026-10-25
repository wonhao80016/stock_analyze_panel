import hashlib
import json
from pathlib import Path

FIXTURE_PATH = Path(__file__).parents[2] / "docs" / "taiwan_market_contract_v1_1.json"
FIXTURE_BYTES = FIXTURE_PATH.read_bytes()
FIXTURE = json.loads(FIXTURE_BYTES)
FIXTURE_SHA256 = "a76aee8a566d838c582d2281b0cdc18b8941ea97e2f4780ea6693775fd97ee28"


def test_taiwan_market_contract_v1_1_evidence_and_share_concepts():
    insufficient = FIXTURE["insufficient_availability"]
    assert insufficient == {
        "availability_policy": "insufficient",
        "available_at": None,
        "historically_available": False,
    }
    exact = FIXTURE["exact_evidence"]
    assert exact["availability_policy"] == "exact_timestamp"
    assert exact["availability_evidence_identifier"] == "official-stable-id"
    shares = FIXTURE["share_capital"]
    assert shares["issued_shares"] != shares["float_shares"]
    assert shares["issued_equals_float"] is False
    assert shares["capital_implies_float"] is False
    assert hashlib.sha256(FIXTURE_BYTES).hexdigest() == FIXTURE_SHA256
