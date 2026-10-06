import json
import tempfile
import unittest
from pathlib import Path

from foundry_inventory.harness import load_inventory, validate_inventory


class HarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = json.loads(
            Path("inventory/inventory.json").read_text(encoding="utf-8")
        )

    def test_inventory_contract(self) -> None:
        results = validate_inventory(self.inventory)
        contract = next(result for result in results if result.scenario == "inventory_contract")
        self.assertTrue(contract.passed)
        self.assertEqual(contract.details["agent_count"], 6)

    def test_workflow_references_are_read_only(self) -> None:
        results = validate_inventory(self.inventory)
        graph = next(result for result in results if result.scenario == "workflow_graph")
        self.assertTrue(graph.passed)
        references = graph.details["references"]
        self.assertEqual(len(references), 4)
        self.assertTrue(all(reference["target"] for reference in references))

    def test_inventory_loader_rejects_non_object_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "JSON object"):
                load_inventory(path)


if __name__ == "__main__":
    unittest.main()
