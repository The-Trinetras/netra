"""Access-code exchange contract (D-CRED): schema, examples and the code format."""

import json
from pathlib import Path

import pytest

from netra_api.identity.access_codes import (
    ALPHABET,
    CODE_LENGTH,
    CredentialIssued,
    ExchangeRequest,
    access_code_digest,
    generate_access_code,
    normalize_access_code,
)

CONTRACTS = Path(__file__).resolve().parents[3] / "shared" / "contracts"
SCHEMA = json.loads((CONTRACTS / "http" / "v1" / "device_credential.schema.json").read_text())


@pytest.mark.parametrize("definition, model", [("ExchangeRequest", ExchangeRequest), ("CredentialIssued", CredentialIssued)])
def test_models_have_exactly_the_schema_fields(definition, model):
    shape = SCHEMA["$defs"][definition]
    assert shape["additionalProperties"] is False
    assert set(shape["properties"]) == set(model.model_fields)
    assert set(shape["required"]) == {name for name, field in model.model_fields.items() if field.is_required()}


def test_committed_examples_parse_and_the_example_code_is_well_formed():
    request = ExchangeRequest.model_validate_json((CONTRACTS / "examples" / "http" / "device_credential_request.json").read_text())
    CredentialIssued.model_validate_json((CONTRACTS / "examples" / "http" / "device_credential_response.json").read_text())
    assert normalize_access_code(request.access_code) == "7K3QM9TX2BWD"


def test_unknown_fields_are_rejected():
    with pytest.raises(ValueError):
        ExchangeRequest.model_validate({"access_code": "7K3Q-M9TX-2BWD", "request_id": "2b8f0c6e-5a4d-4f1b-9c7e-3d2a1b0f9e84", "account_id": "x"})


def test_alphabet_is_crockford_base32():
    assert len(ALPHABET) == 32 and len(set(ALPHABET)) == 32
    assert not set("ILOU") & set(ALPHABET)


def test_generated_codes_are_grouped_and_normalize_to_themselves():
    codes = {generate_access_code() for _ in range(200)}
    assert len(codes) == 200
    for code in codes:
        groups = code.split("-")
        assert [len(g) for g in groups] == [4, 4, 4]
        assert normalize_access_code(code) == "".join(groups)


@pytest.mark.parametrize("typed", ["7k3q-m9tx-2bwd", " 7K3Q M9TX 2BWD ", "7K3QM9TX2BWD", "7K3Q--M9TX-2BWD"])
def test_case_spaces_and_hyphens_are_ignored(typed):
    assert normalize_access_code(typed) == "7K3QM9TX2BWD"


def test_confusable_letters_read_as_digits():
    assert normalize_access_code("OO1L-ilo0-2BWD") == "001111002BWD"


@pytest.mark.parametrize("typed", ["", "7K3Q-M9TX-2BW", "7K3Q-M9TX-2BWDD", "7K3Q-M9TX-2BWU", "7K3Q_M9TX_2BWD", "7K3Q-M9TX-2BW!"])
def test_malformed_codes_are_rejected(typed):
    assert normalize_access_code(typed) is None
    with pytest.raises(ValueError):
        access_code_digest(typed)


def test_digest_is_of_the_normalized_code():
    assert access_code_digest("7k3q m9tx 2bwd") == access_code_digest("7K3Q-M9TX-2BWD")
    assert access_code_digest("7K3Q-M9TX-2BWD") != access_code_digest("7K3Q-M9TX-2BWE")
    assert CODE_LENGTH * 5 >= 60
