#!/usr/bin/env python3
"""Execute one existing Foundry agent without changing its configuration.

This is an explicit, opt-in demonstration. It is not a general agent runner and
it does not create, update, or delete agents.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from azure.ai.agents import AgentsClient
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def authenticate() -> tuple[Any, str]:
    """Use Azure CLI when the configured service principal is unavailable."""
    try:
        credential = DefaultAzureCredential()
        credential.get_token("https://ai.azure.com/.default")
        return credential, "service_principal_or_azure_cli"
    except (ClientAuthenticationError, ValueError):
        credential = DefaultAzureCredential(exclude_environment_credential=True)
        credential.get_token("https://ai.azure.com/.default")
        return credential, "azure_cli"


def load_environment() -> dict[str, str]:
    load_dotenv(ROOT / ".env", override=False)
    required = ("PROJECT_ENDPOINT",)
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))
    return {name: os.getenv(name, "").strip() for name in required}


def load_inventory(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        inventory = json.load(handle)
    if not isinstance(inventory, dict):
        raise ValueError("Inventory root must be a JSON object")
    return inventory


def find_agent(inventory: dict[str, Any], agent_id: str) -> dict[str, Any]:
    matches = [
        agent for agent in inventory.get("agents", [])
        if isinstance(agent, dict) and agent.get("id") == agent_id
    ]
    if not matches:
        raise ValueError(f"Agent not found in inventory: {agent_id}")
    agent = matches[0]
    if agent.get("state") != "enabled":
        raise ValueError(f"Agent is not enabled: {agent_id}")
    if agent.get("definition_kind") != "prompt":
        raise ValueError(f"Agent is a workflow, not a prompt agent: {agent_id}")
    return agent


def execute_agent(
    endpoint: str,
    agent_id: str,
    assistant_id: str,
    prompt: str,
    *,
    credential: Any,
) -> dict[str, Any]:
    """Create one thread and run it using the existing agent configuration."""
    client = AgentsClient(endpoint, credential)
    try:
        run = client.create_thread_and_run(
            agent_id=agent_id,
            body={
                "role": "user",
                "content": prompt,
                "assistant_id": assistant_id,
            },
        )
        return {
            "agent_id": agent_id,
            "thread_id": run.thread_id,
            "run_id": run.id,
            "status": run.status,
            "created_at": run.created_at,
            "last_error": run.last_error,
            "model": run.model,
        }
    finally:
        client.close()


def build_trace(
    inventory: dict[str, Any],
    agent: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": {
            "project_name": inventory.get("project_name"),
            "project_endpoint": inventory.get("project_endpoint"),
        },
        "scenario": "single_agent_demo",
        "policy": {
            "agent_configuration_changed": False,
            "agent_created": False,
            "agent_updated": False,
            "agent_deleted": False,
            "agent_invocation": "performed_once",
            "resource_mutation": "not_performed",
            "secret_values": "not_recorded",
            "response_content": "not_recorded",
        },
        "agent": {
            "id": agent.get("id"),
            "name": agent.get("name"),
            "version": agent.get("version"),
            "model": agent.get("model"),
            "definition_kind": agent.get("definition_kind"),
        },
        "execution": result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one existing Foundry agent read-only")
    parser.add_argument("--inventory", type=Path, required=True, help="Generated inventory JSON")
    parser.add_argument("--agent-id", required=True, help="Existing prompt agent ID")
    parser.add_argument("--prompt", required=True, help="Single demonstration prompt")
    parser.add_argument("--trace", type=Path, required=True, help="Trace JSON output")
    args = parser.parse_args()

    try:
        if not args.prompt.strip():
            raise ValueError("Prompt must not be empty")
        inventory = load_inventory(args.inventory)
        agent = find_agent(inventory, args.agent_id)
        environment = load_environment()
        credential, identity_mode = authenticate()
        result = execute_agent(
            environment["PROJECT_ENDPOINT"],
            args.agent_id,
            str(agent.get("agent_guid") or ""),
            args.prompt.strip(),
            credential=credential,
        )
        trace = build_trace(inventory, agent, result)
        trace["identity_mode"] = identity_mode
        args.trace.parent.mkdir(parents=True, exist_ok=True)
        args.trace.write_text(json.dumps(trace, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Agent execution completed: {result['agent_id']}")
        print(f"Thread ID: {result['thread_id']}")
        print(f"Run ID: {result['run_id']}")
        print(f"Status: {result['status']}")
        print(f"Trace: {args.trace}")
        return 0 if result.get("status") in {"completed", "succeeded"} else 1
    except (ClientAuthenticationError, HttpResponseError, ValueError) as exc:
        print(f"Agent demonstration failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Agent demonstration failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
