#!/usr/bin/env python3
"""Read-only Azure AI Foundry project inventory generator.

The script never creates, updates, deletes, or invokes agents. It only reads
resources exposed by the installed SDK and writes sanitized JSON/Markdown files.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from azure.ai.projects import AIProjectClient
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ENV = (
    "AZURE_TENANT_ID",
    "AZURE_SUBSCRIPTION_ID",
    "AZURE_RESOURCE_GROUP",
    "FOUNDRY_RESOURCE_NAME",
    "FOUNDRY_PROJECT_NAME",
    "PROJECT_ENDPOINT",
)
OPTIONAL_ENV = (
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    "APPLICATIONINSIGHTS_CONNECTION_STRING",
)


def load_config() -> dict[str, str]:
    load_dotenv(ROOT / ".env", override=False)
    missing = [name for name in REQUIRED_ENV if not os.getenv(name, "").strip()]
    if missing:
        raise RuntimeError(
            "Missing required environment variables: " + ", ".join(missing)
        )
    return {name: os.getenv(name, "").strip() for name in REQUIRED_ENV}


def authenticate() -> tuple[DefaultAzureCredential, str]:
    """Use service principal credentials first, then Azure CLI when needed."""
    try:
        credential = DefaultAzureCredential()
        credential.get_token("https://ai.azure.com/.default")
        return credential, "service_principal_or_azure_cli"
    except (ClientAuthenticationError, ValueError):
        credential = DefaultAzureCredential(exclude_environment_credential=True)
        credential.get_token("https://ai.azure.com/.default")
        return credential, "azure_cli"


def redact(value: Any, key: str | None = None) -> Any:
    """Remove values whose key looks secret-like, while preserving resource metadata."""
    secret_keys = {
        "api_key", "apikey", "authorization", "credential", "client_secret",
        "password", "token", "access_key", "secret_key", "connection_string",
    }
    if isinstance(value, dict):
        return {
            k: "[REDACTED]" if k.lower() in secret_keys or any(
                token in k.lower() for token in secret_keys
            ) else redact(v, k)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "model_dump"):
        return redact(value.model_dump(mode="json", exclude_none=True))
    if hasattr(value, "as_dict"):
        return redact(value.as_dict())
    return str(value)


def model_to_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    return {"value": redact(value)}


def safe_id(value: Any) -> str:
    text = str(value or "unknown").strip().replace("/", "_").replace("\\", "_")
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text)[:160]


def iter_pages(operation: Any, *, label: str) -> Iterable[Any]:
    try:
        for item in operation():
            yield item
    except HttpResponseError as exc:
        raise RuntimeError(f"Unable to list {label}: {exc.message}") from exc


def list_resources(project_client: AIProjectClient) -> dict[str, list[dict[str, Any]]]:
    """Read every resource family exposed by the installed project client."""
    operations = {
        "agents": project_client.agents,
        "connections": project_client.connections,
        "deployments": project_client.deployments,
        "datasets": project_client.datasets,
        "evaluation_rules": project_client.evaluation_rules,
        "indexes": project_client.indexes,
        "toolboxes": project_client.toolboxes,
    }
    resources: dict[str, list[dict[str, Any]]] = {}
    for kind, operation in operations.items():
        try:
            items = list(iter_pages(operation.list, label=kind))
        except AttributeError:
            items = []
        resources[kind] = [model_to_dict(item) for item in items]
    return resources


def connection_summary(connection: dict[str, Any]) -> dict[str, Any]:
    raw = redact(connection)
    return {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "type": raw.get("type"),
        "target": raw.get("target"),
        "authentication": raw.get("authentication"),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "metadata": raw.get("metadata"),
    }


def deployment_summary(deployment: dict[str, Any]) -> dict[str, Any]:
    raw = redact(deployment)
    return {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "model": raw.get("model"),
        "version": raw.get("version"),
        "sku": raw.get("sku"),
        "capacity": raw.get("capacity"),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
    }


def latest_version(agent: dict[str, Any]) -> dict[str, Any]:
    """Return the latest version object, including classic prompt and workflow agents."""
    versions = agent.get("versions") or {}
    latest = versions.get("latest") or {}
    if not isinstance(latest, dict):
        return {}
    return latest


def workflow_agent_names(workflow: Any) -> list[dict[str, str]]:
    """Extract only agent names referenced by InvokeAzureAgent nodes."""
    text = workflow if isinstance(workflow, str) else ""
    references: list[dict[str, str]] = []
    for match in re.finditer(
        r"kind:\s*InvokeAzureAgent\s*\n(?:.*\n)*?\s*agent:\s*\n\s*name:\s*([^\s#]+)",
        text,
    ):
        target = match.group(1).strip().strip('"\'')
        if target:
            references.append({"target_agent_name": target})
    return references


def agent_summary(agent: dict[str, Any]) -> dict[str, Any]:
    raw = redact(agent)
    latest = latest_version(raw)
    definition = latest.get("definition") or {}
    if not isinstance(definition, dict):
        definition = {}
    workflow = definition.get("workflow")
    blueprint = latest.get("blueprint") or {}
    instance_identity = latest.get("instance_identity") or {}
    return {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "state": raw.get("state"),
        "version": latest.get("version") or raw.get("version"),
        "definition_kind": definition.get("kind"),
        "model": definition.get("model"),
        "deployment": raw.get("deployment"),
        "instructions": definition.get("instructions"),
        "reasoning": definition.get("reasoning"),
        "tools": definition.get("tools") or [],
        "workflow": workflow,
        "connected_agents": workflow_agent_names(workflow),
        "blueprint": blueprint,
        "instance_identity": instance_identity,
        "metadata": latest.get("metadata") or raw.get("metadata"),
        "created_at": latest.get("created_at") or raw.get("created_at"),
        "updated_at": latest.get("modified_at") or raw.get("updated_at"),
        "agent_guid": latest.get("agent_guid"),
        "blueprint_reference": latest.get("blueprint_reference"),
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def make_architecture(inventory: dict[str, Any]) -> str:
    agents = inventory["agents"]
    connections = inventory["connections"]
    lines = [
        "# Azure AI Foundry Architecture",
        "",
        "> Generated by the read-only Foundry inventory extractor. No agent was created, changed, deleted, or executed.",
        "",
        "## Project",
        "",
        f"- Project endpoint: `{inventory['project_endpoint']}`",
        f"- Identity mode: `{inventory['identity']['identity_mode']}`",
        f"- Inventory generated: `{inventory['generated_at']}`",
        "",
        "## Graph",
        "",
        "```mermaid",
        "flowchart LR",
        "    subgraph Agents[Agents]",
    ]
    for agent in agents:
        lines.append(f"        A{safe_id(agent.get('id'))}[{agent.get('name') or agent.get('id')}]" )
    lines.extend(["    end", "    subgraph Tools[Tools and resources]", "        T[Tools / tool resources]", "    end", "    subgraph Connections[Connections]", "        C[Connections]", "    end"])
    for agent in agents:
        for delegated in agent.get("connected_agents", []) or []:
            target = delegated.get("target_agent_name")
            if target:
                lines.append(f"        A{safe_id(agent.get('id'))} -->|InvokeAzureAgent| A{safe_id(target)}")
    for agent in agents:
        lines.append(f"        A{safe_id(agent.get('id'))} -->|tools| T")
    for connection in connections:
        lines.append(f"        C -->|{connection.get('name') or connection.get('type')}| T")
    lines.extend(["    end", "```", "", "## Agent fichas", ""])
    if not agents:
        lines.append("No agents were found.")
    for agent in agents:
        delegated = agent.get("connected_agents", []) or []
        lines.extend([
            f"### {agent.get('name') or agent.get('id')}",
            "",
            f"- ID: `{agent.get('id')}`",
            f"- State: `{agent.get('state') or 'not reported'}`",
            f"- Version: `{agent.get('version') or 'not reported'}`",
            f"- Definition kind: `{agent.get('definition_kind') or 'not reported'}`",
            f"- Model: `{agent.get('model') or 'not reported'}`",
            f"- Deployment: `{agent.get('deployment') or 'not reported'}`",
            f"- Instructions: `{agent.get('instructions') or 'not reported'}`",
            f"- Reasoning: `{json.dumps(agent.get('reasoning') or {}, ensure_ascii=False)}`",
            "",
            "#### Tools",
            "",
            f"`{json.dumps(agent.get('tools') or [], ensure_ascii=False)}`",
            "",
            "#### Workflow references",
            "",
            f"`{json.dumps(delegated, ensure_ascii=False)}`",
            "",
            "#### Blueprint and identity",
            "",
            f"- Blueprint: `{json.dumps(agent.get('blueprint_reference') or {}, ensure_ascii=False)}`",
            f"- Blueprint identity: `{json.dumps(agent.get('blueprint') or {}, ensure_ascii=False)}`",
            f"- Instance identity: `{json.dumps(agent.get('instance_identity') or {}, ensure_ascii=False)}`",
            "",
            "#### Metadata",
            "",
            f"`{json.dumps(agent.get('metadata') or {}, ensure_ascii=False)}`",
            "",
        ])
    missing_targets = sum(1 for a in agents for d in a.get("connected_agents", []) or [] if not d.get("target_agent_name"))
    lines.extend(["## Hallazgos", "", f"- References to missing targets: {missing_targets}", "- Agent records without IDs: " + str(sum(1 for a in agents if not a.get("id"))), "- Connections without a target: " + str(sum(1 for c in connections if not c.get("target"))), ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a read-only Azure AI Foundry inventory")
    parser.add_argument("--output", type=Path, default=ROOT / "inventory", help="Directory for generated inventory")
    args = parser.parse_args()
    try:
        config = load_config()
        credential, identity_mode = authenticate()
        token = credential.get_token("https://ai.azure.com/.default")
        project_client = AIProjectClient(config["PROJECT_ENDPOINT"], credential)
        try:
            resources = list_resources(project_client)
            identity = {
                "credential_type": type(credential).__name__,
                "identity_mode": identity_mode,
                "token_acquired": bool(token.token),
                "token_expires_on": datetime.fromtimestamp(token.expires_on, timezone.utc).isoformat(),
                "project_endpoint": config["PROJECT_ENDPOINT"],
                "project_name": config["FOUNDRY_PROJECT_NAME"],
                "resource_group": config["AZURE_RESOURCE_GROUP"],
                "subscription_id": config["AZURE_SUBSCRIPTION_ID"],
                "tenant_id": config["AZURE_TENANT_ID"],
            }
            inventory = {
                "schema_version": 1,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "project_endpoint": config["PROJECT_ENDPOINT"],
                "project_name": config["FOUNDRY_PROJECT_NAME"],
                "identity": identity,
                "agents": [agent_summary(item) for item in resources["agents"]],
                "connections": [connection_summary(item) for item in resources["connections"]],
                "deployments": [deployment_summary(item) for item in resources["deployments"]],
                "datasets": resources["datasets"],
                "evaluation_rules": resources["evaluation_rules"],
                "indexes": resources["indexes"],
                "toolboxes": resources["toolboxes"],
                "resource_counts": {kind: len(items) for kind, items in resources.items()},
            }
            raw_dir = args.output / "raw"
            for kind, items in resources.items():
                for item in items:
                    item_id = item.get("id") or item.get("name") or safe_id(kind)
                    write_json(raw_dir / kind / f"{safe_id(item_id)}.json", redact(item))
            write_json(args.output / "inventory.json", inventory)
            write_text(args.output / "ARCHITECTURE.md", make_architecture(inventory))
            print(f"Inventory generated at: {args.output / 'inventory.json'}")
            print(f"Agents: {inventory['resource_counts']['agents']}")
            print(f"Connections: {inventory['resource_counts']['connections']}")
            print(f"Deployments: {inventory['resource_counts']['deployments']}")
            print(f"Resources: {inventory['resource_counts']}")
            return 0
        finally:
            project_client.close()
    except Exception as exc:
        print(f"Inventory extraction failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
