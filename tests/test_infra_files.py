"""Structural checks on Dockerfile / Terraform / workflows. They do NOT validate that the IaC or CI works
(no terraform, docker or GitHub here); they catch regressions in properties we decided on (ADR-0007)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
TF = ROOT / "infra" / "terraform"


def _workflow(name: str) -> Any:
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())


def _tf_text() -> str:
    return "\n".join(p.read_text() for p in sorted(TF.glob("*.tf")))


def test_ci_runs_pytest_and_mypy() -> None:
    wf = _workflow("ci.yml")
    steps = " ".join(str(s.get("run", "")) for s in wf["jobs"]["test"]["steps"])
    assert "pytest" in steps and "mypy" in steps
    assert set(wf["jobs"]) >= {"test", "security", "terraform", "iac-scan", "docker"}


def test_ci_least_privilege_and_no_cloud_secrets() -> None:
    raw = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert _workflow("ci.yml")["permissions"] == {"contents": "read"}
    assert "${{ secrets." not in raw and "AZURE_CLIENT_SECRET" not in raw


def test_deploy_uses_oidc_manual_only_no_secrets() -> None:
    raw = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()
    wf = _workflow("deploy.yml")
    on = wf.get(True, wf.get("on"))  # PyYAML parses bare `on` as True
    assert set(on) == {"workflow_dispatch"}
    assert wf["permissions"]["id-token"] == "write"
    assert "${{ secrets." not in raw and "client-secret" not in raw
    assert "environment:" in raw  # approval gate lives on the environment


def test_all_workflows_marked_unvalidated() -> None:
    for p in (ROOT / ".github" / "workflows").glob("*.yml"):
        assert "UNVALIDATED" in p.read_text()[:300], p.name
    assert "UNVALIDATED" in (ROOT / "Dockerfile").read_text()[:300]
    assert "UNVALIDATED" in (TF / "versions.tf").read_text()[:300]


def test_dockerfile_non_root_no_secrets_pinned_tag() -> None:
    d = (ROOT / "Dockerfile").read_text()
    users = re.findall(r"^USER\s+(\S+)", d, re.M)
    assert users and users[-1] not in {"root", "0", "0:0"}
    assert not re.search(r"^\s*(ENV|ARG)\s+\S*(KEY|TOKEN|SECRET|PASSWORD)", d, re.M | re.I)
    froms = re.findall(r"^FROM\s+(\S+)", d, re.M)
    assert froms and all(":" in f and not f.endswith(":latest") for f in froms)
    assert "HEALTHCHECK" in d


def test_dockerignore_excludes_secrets_and_state() -> None:
    di = (ROOT / ".dockerignore").read_text()
    for needle in (".env", "*.tfstate*", "data", ".git"):
        assert needle in di


def test_gitignore_covers_secrets_and_state() -> None:
    gi = (ROOT / ".gitignore").read_text()
    for needle in (".env", ".terraform/", "*.tfstate", "terraform.tfvars", "data/", "__pycache__/"):
        assert needle in gi


def test_terraform_variables_and_locals_are_declared() -> None:
    text = _tf_text()
    declared = set(re.findall(r'^variable\s+"(\w+)"', text, re.M))
    used = set(re.findall(r"\bvar\.(\w+)", text))
    assert used <= declared, used - declared
    locals_block = re.search(r"locals\s*\{(.*?)\n\}", text, re.S)
    assert locals_block
    local_names = set(re.findall(r"^\s*(\w+)\s*=", locals_block.group(1), re.M))
    assert set(re.findall(r"\blocal\.(\w+)", text)) <= local_names


def test_terraform_security_properties() -> None:
    t = _tf_text()
    assert re.search(r"admin_enabled\s+=\s+false", t)
    assert re.search(r"rbac_authorization_enabled\s+=\s+true", t)
    assert re.search(r"allow_insecure_connections\s+=\s+false", t)
    assert re.search(r'variable "external_ingress".*?default\s*=\s*false', t, re.S)
    assert "azurerm_user_assigned_identity" in t and "UserAssigned" in t
    # no secret value is ever written by Terraform
    assert "azurerm_key_vault_secret" not in t
    assert not re.search(r"sk-ant-|ANTHROPIC_API_KEY\s*=\s*\"", t)


def test_terraform_resource_refs_exist() -> None:
    t = _tf_text()
    defined = set(re.findall(r'^resource\s+"(\w+)"\s+"(\w+)"', t, re.M))
    refs = set(re.findall(r"\b(azurerm_\w+|random_\w+)\.(\w+)\b", t))
    refs = {r for r in refs if r[0] not in {"azurerm_client_config"}}
    missing = {r for r in refs if r not in defined and r[1] not in {"current"}}
    assert not missing, missing


def test_requirements_pinned() -> None:
    for line in (ROOT / "requirements.txt").read_text().splitlines():
        if line and not line.startswith("#"):
            assert "==" in line, line
