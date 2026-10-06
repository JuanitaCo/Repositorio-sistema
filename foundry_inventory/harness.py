#!/usr/bin/env python3
"""Read-only validation harness for the Foundry inventory.

The harness validates the inventory and workflow references. It never calls an
agent, creates resources, updates resources, or changes the project.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ValidationResult:
    """A compact, traceable result for one validation scenario."""

    scenario: str
    passed: bool
    checks: tuple[str, bool]
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "passed": self.passed,
            "checks": {name: passed for name, passed in self.checks},
            "details": self.details,
        }


def load_inventory(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        inventory = json.load(handle)
    if not isinstance(inventory, dict):
        raise ValueError("Inventory root must be a JSON object")
    return inventory


def validate_inventory(inventory: dict[str, Any]) -> list[ValidationResult]:
    agents = inventory.get("agents")
    if not isinstance(agents, list):
        raise ValueError("Inventory must contain an agents list")

    names = {agent.get("name") for agent in agents if isinstance(agent, dict) and agent.get("name")}
    results: list[ValidationResult] = []

    def check(name: str, passed: bool) -> None:
        checks.append((name, passed))

    checks: list[tuple[str, bool]] = []
    check("agent_count", len(agents) == 6)
    check("agent_ids", all(isinstance(agent.get("id"), str) and agent.get("id") for agent in agents))
    check("agent_versions", all(agent.get("version") for agent in agents))
    check("agent_definitions", all(agent.get("definition_kind") for agent in agents))
    check("prompt_models", all(agent.get("model") for agent in agents if agent.get("definition_kind") == "prompt"))
    check("workflow_definitions", all(agent.get("workflow") for agent in agents if agent.get("definition_kind") == "workflow"))
    check("tool_fields", all("tools" in agent for agent in agents))
    check("identity_fields", all("blueprint" in agent and "instance_identity" in agent for agent in agents))
    results.append(ValidationResult("inventory_contract", all(p for _, p in checks), tuple(checks), {"agent_count": len(agents)}))

    references: list[dict[str, Any]] = []
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        for reference in agent.get("connected_agents") or []:
            if isinstance(reference, dict):
                references.append({"source": agent.get("id"), "target": reference.get("target_agent_name")})

    check("workflow_references", bool(references))
    check("workflow_targets_exist", all(reference["target"] in names for reference in references))
    check("workflow_targets_are_prompt_agents", all(
        next((agent.get("definition_kind") for agent in agents if agent.get("name") == reference["target"]), None) == "prompt"
        for reference in references
    ))
    results.append(ValidationResult(
        "workflow_graph",
        all(p for _, p in checks),
        tuple(checks),
        {"references": references},
    ))

    check("no_agent_invocation_api", not any(
        "invoke" in str(agent.get("metadata", "")).lower() for agent in agents
    ))
    results.append(ValidationResult(
        "read_only_policy",
        all(p for _, p in checks),
        tuple(checks),
        {"mutation_operations": []},
    ))

    return results


def build_trace(results: list[ValidationResult], inventory: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": {
            "project_name": inventory.get("project_name"),
            "project_endpoint": inventory.get("project_endpoint"),
        },
        "policy": {
            "agent_invocation": "not_performed",
            "resource_mutation": "not_performed",
            "secret_values": "not_recorded",
        },
        "scenarios": [result.as_dict() for result in results],
    }


def write_trace(path: Path, trace: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(trace, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run read-only Foundry inventory validations")
    parser.add_argument("--inventory", type=Path, required=True, help="Generated inventory JSON")
    parser.add_argument("--trace", type=Path, required=True, help="Trace JSON output")
    args = parser.parse_args()

    try:
        inventory = load_inventory(args.inventory)
        results = validate_inventory(inventory)
        trace = build_trace(results, inventory)
        write_trace(args.trace, trace)
        failed = sum(not result.passed for result in results)
        for result in results:
            print(f"{'PASS' if result.passed else 'FAIL'} {result.scenario}: {len(result.checks)} checks")
        print(f"Result: {len(results) - failed} passed, {failed} failed")
        return 1 if failed else 0
    except Exception as exc:
        print(f"Harness validation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
