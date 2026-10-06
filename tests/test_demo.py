import unittest

from foundry_inventory.demo import find_agent, load_inventory
from pathlib import Path


class DemoContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = load_inventory(Path("inventory/inventory.json"))

    def test_only_enabled_prompt_agents_can_be_executed(self) -> None:
        agent = find_agent(self.inventory, "A2-Experto-exposomas")
        self.assertEqual(agent["state"], "enabled")
        self.assertEqual(agent["definition_kind"], "prompt")

    def test_workflow_agent_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "workflow"):
            find_agent(self.inventory, "prototipochiquito")


if __name__ == "__main__":
    unittest.main()
