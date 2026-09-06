"""Deterministic modifier comparison helpers; no Blender scene access here."""

EXCLUDED_PROPERTIES = {
    "rna_type", "name", "type", "show_expanded", "is_active", "use_pin_to_last",
}
SCALAR_TYPES = {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}


def _plain_value(value, prop_type, is_array, is_id):
    if prop_type == "POINTER":
        if value is None:
            return None
        if not is_id(value):
            raise TypeError("embedded pointer")
        return {"name": value.name, "type": value.bl_rna.identifier}
    if prop_type == "ENUM" and isinstance(value, set):
        return sorted(value)
    if is_array:
        return list(value)
    if type(value) in (bool, int, float, str) or value is None:
        return value
    raise TypeError(f"unsupported value {type(value).__name__}")


def compare_modifier_properties(current, fresh, is_id=lambda _value: False):
    """Compare user-facing RNA values against a freshly-created modifier."""
    changed = []
    limitations = []
    for prop in current.bl_rna.properties:
        identifier = prop.identifier
        if getattr(current, "type", None) == "BEVEL":
            if identifier == "width_pct" and current.offset_type != "PERCENT":
                continue
            if identifier == "width" and current.offset_type == "PERCENT":
                continue
        if (identifier in EXCLUDED_PROPERTIES or prop.is_readonly or prop.is_hidden
                or prop.is_skip_save or current.is_property_hidden(identifier)):
            continue
        prop_type = prop.type
        is_array = bool(getattr(prop, "is_array", False))
        if prop_type not in SCALAR_TYPES | {"POINTER"}:
            limitations.append({"property": identifier, "reason": f"Unsupported RNA type: {prop_type}"})
            continue
        try:
            current_value = _plain_value(getattr(current, identifier), prop_type, is_array, is_id)
            default_value = _plain_value(getattr(fresh, identifier), prop_type, is_array, is_id)
        except (AttributeError, TypeError, ValueError) as exc:
            limitations.append({"property": identifier, "reason": str(exc)})
            continue
        if current_value != default_value:
            changed.append({
                "property": identifier,
                "label": prop.name or identifier,
                "value_type": prop_type,
                "default": default_value,
                "current": current_value,
            })
    return changed, limitations
