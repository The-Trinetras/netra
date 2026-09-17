import importlib.util
import json
from pathlib import Path

import pytest
from botocore.exceptions import ClientError


SCRIPT = Path(__file__).parents[2] / "infrastructure" / "aws" / "bootstrap_database_identity.py"
SPEC = importlib.util.spec_from_file_location("bootstrap_database_identity", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeSecretsManager:
    def __init__(self):
        self.values = {}
        self.generated = 0

    def get_secret_value(self, *, SecretId):
        if SecretId not in self.values:
            raise ClientError(
                {"Error": {"Code": "ResourceNotFoundException", "Message": "missing"}},
                "GetSecretValue",
            )
        return {"SecretString": self.values[SecretId]}

    def get_random_password(self, **kwargs):
        assert kwargs == {"PasswordLength": 40, "ExcludePunctuation": True}
        self.generated += 1
        return {"RandomPassword": "a" * 40}

    def put_secret_value(self, *, SecretId, SecretString):
        self.values[SecretId] = SecretString


def test_application_identity_is_generated_once_and_reused():
    client = FakeSecretsManager()
    first = MODULE._application_identity(client, "application", "netra_app")
    second = MODULE._application_identity(client, "application", "netra_app")

    assert first == second == {"username": "netra_app", "password": "a" * 40}
    assert client.generated == 1
    assert json.loads(client.values["application"]) == first


def test_existing_secret_with_wrong_role_fails_closed():
    client = FakeSecretsManager()
    client.values["application"] = json.dumps({"username": "other", "password": "secret"})

    with pytest.raises(RuntimeError, match="username does not match"):
        MODULE._application_identity(client, "application", "netra_app")
