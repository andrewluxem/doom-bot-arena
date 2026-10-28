"""Schema 4 `attack` (docs/MATCH_FORMAT.md section 5): the validator's row rules."""

from engine import bundle
from engine.validate_bundle import check_rows_v4


def row(tic, alive=True, attack=False):
    return {"tic": tic, "slot": 1, "alive": alive, "attack": attack}


def test_schema_4_adds_attack_only():
    assert bundle.ROW_FIELDS[4] - bundle.ROW_FIELDS[3] == {"attack"}
    assert bundle.SCHEMA_VERSION == 4 and 4 in bundle.SCHEMA_VERSIONS


def test_attack_rules():
    assert check_rows_v4([row(0, attack=True), row(1), row(2, alive=False)], 3) == []
    assert "not a bool" in check_rows_v4([row(0, attack=1)], 3)[0]
    assert "dead row" in check_rows_v4([row(0, alive=False, attack=True)], 3)[0]
    assert "last tic" in check_rows_v4([row(2, attack=True)], 3)[0]
