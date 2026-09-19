"""Deployment files (I4): images, Compose and nginx follow the runtime and data rules."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCKER = ROOT / "infrastructure" / "docker"
COMPOSE = ROOT / "infrastructure" / "compose" / "docker-compose.yml"
NGINX = ROOT / "infrastructure" / "reverse-proxy" / "nginx.conf"
IMAGES = ("api.Dockerfile", "worker.Dockerfile")


def _dockerfile(name: str) -> str:
    return (DOCKER / name).read_text(encoding="utf-8")


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", IMAGES)
def test_images_use_the_pinned_python_and_the_uv_that_wrote_the_lock(name):
    text = _dockerfile(name)
    python = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    assert f"FROM python:{python}-slim-bookworm" in text
    assert "UV_PYTHON_DOWNLOADS=never" in text
    uv_images = re.findall(r"FROM ghcr\.io/astral-sh/uv:([0-9.]+)", text)
    assert uv_images and all(tag.count(".") == 2 for tag in uv_images)  # exact version, never "latest"
    assert ":latest" not in text


@pytest.mark.parametrize("name", IMAGES)
def test_images_install_only_from_the_committed_lock_without_dev_tools(name):
    text = _dockerfile(name)
    installs = [line for line in text.splitlines() if line.strip().startswith("RUN") and "uv " in line]
    assert installs and all("uv sync --locked --no-dev" in line for line in installs)
    assert "pip install" not in text and "requirements.txt" not in text


@pytest.mark.parametrize("name", IMAGES)
def test_images_run_as_a_non_root_user_and_copy_no_secrets(name):
    text = _dockerfile(name)
    assert re.search(r"^USER netra$", text, re.MULTILINE)
    copied = " ".join(line for line in text.splitlines() if line.startswith("COPY"))
    for forbidden in (".env", "tfstate", "tfvars", ".venv"):
        assert forbidden not in copied


def test_only_the_worker_image_carries_tesseract():
    assert "tesseract-ocr" in _dockerfile("worker.Dockerfile")
    assert "tesseract" not in _dockerfile("api.Dockerfile")


def test_compose_runs_api_worker_nginx_and_migrations_but_no_database():
    services = _compose()["services"]
    assert set(services) == {"migrate", "api", "worker", "nginx"}
    images = " ".join(str(service.get("image", "")) for service in services.values())
    assert "postgres" not in images and "pgbouncer" not in images  # D-INFRA: RDS behind PgBouncer


def test_migrations_finish_before_the_api_and_worker_start():
    services = _compose()["services"]
    assert services["migrate"]["command"][-2:] == ["upgrade", "head"]
    assert services["migrate"]["restart"] == "no"
    for name in ("api", "worker"):
        assert services[name]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"


def test_only_nginx_publishes_ports():
    services = _compose()["services"]
    assert [name for name, service in services.items() if service.get("ports")] == ["nginx"]
    assert services["nginx"]["ports"] == ["80:80", "443:443"]


def test_deployment_values_are_required_not_invented():
    text = COMPOSE.read_text(encoding="utf-8")
    for variable in ("NETRA_ENV_FILE", "NETRA_IMAGE_TAG", "NETRA_TLS_DOMAIN", "NETRA_UPLOAD_MAX_BODY"):
        assert re.search(r"\$\{" + variable + r":\?", text), f"{variable} must be required (${{VAR:?...}})"
        assert not re.search(r"\$\{" + variable + r":-", text), f"{variable} must not have a default"


def test_app_containers_read_secrets_from_the_host_env_file_only():
    services = _compose()["services"]
    for name in ("migrate", "api", "worker"):
        service = services[name]
        assert service["env_file"] == ["${NETRA_ENV_FILE:?set NETRA_ENV_FILE to the host env file}"]
        assert not any("KEY" in key or "PASSWORD" in key or "SECRET" in key
                       for key in (service.get("environment") or {}))
        assert service["read_only"] is True


def test_nginx_substitutes_only_netra_variables():
    nginx = _compose()["services"]["nginx"]
    assert nginx["environment"]["NGINX_ENVSUBST_FILTER"] == "^NETRA_"
    template = NGINX.read_text(encoding="utf-8")
    substituted = set(re.findall(r"\$\{([A-Z0-9_]+)\}", template))
    assert substituted == {"NETRA_TLS_DOMAIN", "NETRA_UPLOAD_MAX_BODY"}


def test_nginx_upgrades_the_protocol_websocket_and_redirects_to_https():
    template = NGINX.read_text(encoding="utf-8")
    websocket = template.split("location = /v1/ws")[1].split("}")[0]
    assert "proxy_set_header Upgrade $http_upgrade;" in websocket
    assert 'proxy_set_header Connection "upgrade";' in websocket
    assert "return 301 https://$host$request_uri;" in template
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in template


def test_nginx_keeps_telemetry_counters_off_the_internet():
    template = NGINX.read_text(encoding="utf-8")
    block = template.split("location = /health/telemetry")[1].split("}")[0]
    assert "deny all;" in block


def test_docker_context_excludes_secrets_state_and_local_environments():
    ignored = {line.strip() for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()}
    for pattern in (".env", "**/.venv", "**/*.tfstate", "**/*.tfvars", "**/.terraform", ".git"):
        assert pattern in ignored
