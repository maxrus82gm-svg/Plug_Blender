"""Targeted deterministic modifier comparison tests without bpy."""

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "modifier_inspector", ROOT / "Plugins/AstroModeler/astro_modeler/modifier_inspector.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Prop:
    def __init__(self, identifier, prop_type, *, name=None, array=False,
                 readonly=False, hidden=False, skip_save=False):
        self.identifier = identifier
        self.name = name or identifier.title()
        self.type = prop_type
        self.is_array = array
        self.is_readonly = readonly
        self.is_hidden = hidden
        self.is_skip_save = skip_save


class Modifier:
    def __init__(self, properties, values, dynamically_hidden=()):
        self.bl_rna = type("RNA", (), {"properties": properties})()
        self._hidden = set(dynamically_hidden)
        for key, value in values.items():
            setattr(self, key, value)

    def is_property_hidden(self, identifier):
        return identifier in self._hidden


class ModifierInspectorTests(unittest.TestCase):
    def test_only_meaningful_exact_changes_are_returned(self):
        properties = [
            Prop("enabled", "BOOLEAN"), Prop("segments", "INT"),
            Prop("width", "FLOAT", name="Width"), Prop("mode", "ENUM"),
            Prop("offset", "FLOAT", array=True), Prop("unchanged", "INT"),
            Prop("name", "STRING"), Prop("readonly", "INT", readonly=True),
            Prop("hidden", "INT", hidden=True), Prop("dynamic", "INT"),
            Prop("collection", "COLLECTION"),
        ]
        fresh_values = dict(enabled=False, segments=1, width=0.1, mode="ANGLE",
                            offset=(0.0, 0.0, 0.0), unchanged=7, name="Fresh",
                            readonly=0, hidden=0, dynamic=0, collection=[])
        current_values = dict(fresh_values, enabled=True, segments=6, width=0.003,
                              mode="WEIGHT", offset=(1.0, 0.0, 0.0), name="User",
                              readonly=9, hidden=9, dynamic=9)
        current = Modifier(properties, current_values, dynamically_hidden={"dynamic"})
        fresh = Modifier(properties, fresh_values)

        changed, limitations = module.compare_modifier_properties(current, fresh)

        self.assertEqual([item["property"] for item in changed],
                         ["enabled", "segments", "width", "mode", "offset"])
        self.assertEqual(changed[-1]["default"], [0.0, 0.0, 0.0])
        self.assertEqual(changed[-1]["current"], [1.0, 0.0, 0.0])
        self.assertEqual(limitations, [
            {"property": "collection", "reason": "Unsupported RNA type: COLLECTION"}])

    def test_bevel_width_alias_follows_active_offset_mode(self):
        properties = [Prop("width", "FLOAT"), Prop("width_pct", "FLOAT"),
                      Prop("offset_type", "ENUM")]
        fresh = Modifier(properties, {"width": 0.1, "width_pct": 0.1, "offset_type": "OFFSET"})
        current = Modifier(properties, {"width": 0.003, "width_pct": 0.003, "offset_type": "WIDTH"})
        current.type = "BEVEL"
        changed, limitations = module.compare_modifier_properties(current, fresh)
        self.assertEqual([item["property"] for item in changed], ["width", "offset_type"])
        self.assertEqual(limitations, [])


if __name__ == "__main__":
    unittest.main()
