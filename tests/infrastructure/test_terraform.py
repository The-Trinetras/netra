"""Terraform (I4): backups, network reachability, least privilege and shared state.

Static checks over the HCL; `terraform validate` is run separately (it needs
the provider). These pin the security-relevant decisions so a later edit
cannot silently undo them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TF = ROOT / "infrastructure" / "terraform"


def _read(name: str) -> str:
    return (TF / name).read_text(encoding="utf-8")


def _block(text: str, header: str) -> str:
    """The body of the first HCL block starting with header, by brace matching."""

    start = text.index(header)
    depth, index = 0, text.index("{", start)
    for position in range(index, len(text)):
        depth += {"{": 1, "}": -1}.get(text[position], 0)
        if depth == 0:
            return text[index:position + 1]
    raise AssertionError(f"unterminated block {header}")


def test_rds_backups_are_on_by_default_and_cannot_be_disabled():
    variable = _block(_read("variables.tf"), 'variable "db_backup_retention_days"')
    assert re.search(r"default\s*=\s*7\b", variable)
    assert "var.db_backup_retention_days >= 1" in variable
    assert re.search(r"backup_retention_period\s*=\s*var\.db_backup_retention_days", _read("database.tf"))


def test_pgbouncer_accepts_connections_only_from_the_application_host():
    group = _block(_read("main.tf"), 'resource "aws_security_group" "pgbouncer"')
    ingress = re.findall(r"ingress\s*\{[^}]*\}", group)
    assert len(ingress) == 1
    assert "from_port       = 6432" in ingress[0] and "aws_security_group.app.id" in ingress[0]
    assert "cidr_blocks" not in ingress[0]
    assert "listen_addr = 0.0.0.0" in _read("scripts/bootstrap_pgbouncer.sh.tftpl")


def test_rds_still_accepts_only_pgbouncer():
    group = _block(_read("main.tf"), 'resource "aws_security_group" "rds"')
    assert "aws_security_group.pgbouncer.id" in group and "cidr_blocks" not in group


def test_the_application_host_exposes_only_http_and_https():
    group = _block(_read("main.tf"), 'resource "aws_security_group" "app"')
    ports = sorted(int(port) for port in re.findall(r"from_port\s*=\s*(\d+)", group) if port != "0")
    assert ports == [80, 443]
    assert "22" not in re.findall(r"from_port\s*=\s*(\d+)", group)


def test_the_application_role_is_least_privilege():
    policy = _block(_read("app.tf"), 'data "aws_iam_policy_document" "app"')
    actions = set(re.findall(r'"([a-z0-9]+:[A-Za-z*]+)"', policy))
    assert actions == {"s3:GetObject", "s3:PutObject", "secretsmanager:GetSecretValue",
                       "ssm:GetParameter", "ssm:GetParametersByPath"}
    assert not any(action.endswith(":*") for action in actions)
    assert '"*"' not in policy


def test_application_host_size_is_an_explicit_cost_decision():
    variable = _block(_read("variables.tf"), 'variable "app_instance_type"')
    assert not re.search(r"^\s*default\s*=", variable, re.MULTILINE)


@pytest.mark.parametrize("resource", ['resource "aws_instance" "pgbouncer"', 'resource "aws_instance" "app"'])
def test_running_instances_are_not_replaced_by_ami_drift(resource):
    source = _read("compute.tf") if "pgbouncer" in resource else _read("app.tf")
    block = _block(source, resource)
    assert re.search(r"ignore_changes\s*=\s*\[ami\]", block)
    assert 'http_tokens = "required"' in block


def test_no_secret_value_is_a_terraform_input_or_in_user_data():
    for name in ("app.tf", "variables.tf", "scripts/bootstrap_app.sh.tftpl"):
        text = _read(name)
        assert not re.search(r"(password|secret_string|api_key)\s*=\s*\"[^\"$]", text, re.IGNORECASE), name
    assert "aws_secretsmanager_secret_version" not in _read("app.tf")


def test_rendered_env_file_is_root_only_and_cannot_override_fixed_values():
    script = _read("scripts/bootstrap_app.sh.tftpl")
    assert 'chmod 0600 "$tmp"' in script and "install -d -m 0700 -o root -g root /etc/netra" in script
    for fixed in ("NETRA_DATABASE_URL", "NETRA_STORAGE_PROVIDER", "NETRA_S3_BUCKET", "NETRA_AWS_REGION"):
        assert f'"{fixed}"' in script  # listed in the jq exclusion
    assert 'test("^NETRA_[A-Z0-9_]+$")' in script


def test_state_is_shared_in_s3_with_locking():
    versions = _read("versions.tf")
    assert 'backend "s3" {}' in versions and ">= 1.10.0" in versions
    example = _read("backend.hcl.example")
    assert "use_lockfile = true" in example and "encrypt      = true" in example
    bucket = (TF / "state-bucket" / "main.tf").read_text(encoding="utf-8")
    for required in ('status = "Enabled"', "block_public_policy     = true", "prevent_destroy = true",
                     'sse_algorithm = "AES256"'):
        assert required in bucket


def test_state_plans_variables_and_backend_config_are_never_committed():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for pattern in ("infrastructure/terraform/*.tfstate", "infrastructure/terraform/*.tfstate.*",
                    "infrastructure/terraform/*.tfvars", "infrastructure/terraform/*.tfplan",
                    "infrastructure/terraform/.terraform/", "infrastructure/terraform/backend.hcl",
                    "infrastructure/terraform/state-bucket/*.tfstate"):
        assert pattern in ignored, pattern


def test_linux_executed_files_keep_lf_line_endings():
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    for pattern in ("*.tftpl", "*.sh", "*Dockerfile", "*.conf"):
        assert re.search(re.escape(pattern) + r"\s+text eol=lf", attributes), pattern
    for script in (TF / "scripts").glob("*.tftpl"):
        assert b"\r\n" not in script.read_bytes(), script.name
