# Read-only Foundry inventory harness

This package validates the generated Foundry inventory and records the validation trace. It is intentionally read-only: it does not authenticate to the project, create resources, update resources, delete resources, or invoke agents.

## Usage

```powershell
python -m unittest discover -s tests -v
python -m foundry_inventory.harness --inventory inventory/inventory.json --trace inventory/trace.json
```

The trace records only the inventory identity, scenario results, and the policy that no agent invocation or resource mutation was performed. Secret values are never recorded.

## Scenarios

- `inventory_contract`: structural validation of agents, versions, definitions, models, tools, identity, and blueprint metadata.
- `workflow_graph`: validates that every `InvokeAzureAgent` target exists and is a prompt agent.
- `read_only_policy`: verifies that the harness contains no invocation or mutation operation.

## Single-agent demonstration

The demo is opt-in and executes exactly one existing enabled prompt agent. It creates a temporary thread and run, does not override model, instructions, tools, or other agent configuration, and records only identifiers and status. It does not create, update, or delete agents.

```powershell
python -m foundry_inventory.demo --inventory inventory/inventory.json --agent-id A2-Experto-exposomas --prompt "Analiza la información proporcionada y clasifica los factores relevantes." --trace inventory/demo-trace.json
```

## Safety boundary

The harness accepts an existing inventory file and returns a trace. It never calls `create_agent`, `update_agent`, or `delete_agent`; it does not change the agent configuration and does not persist response content or secret values.
