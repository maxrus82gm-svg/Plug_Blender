"""Deterministic modifier comparison helpers; no Blender scene access here."""

import struct

EXCLUDED_PROPERTIES = {
    "rna_type", "name", "type", "show_expanded", "is_active", "use_pin_to_last",
}
SCALAR_TYPES = {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}

# Modifier Inspector V1 is Russian-first. Keep its user-facing copy here so a
# later RU/EN switch does not require searching through operators and panels.
INSPECTOR_UI_RU = {
    "panel_title": "АНАЛИЗ МОДИФИКАТОРОВ",
    "object": "Объект",
    "none": "Нет",
    "get_modifiers": "Получить модификаторы",
    "modifier": "Модификатор",
    "compare_parameters": "Сравнить параметры",
    "changed_parameters": "Изменено параметров: {count}",
    "default": "По умолчанию",
    "current": "Текущее",
    "ai_explanation": "ОБЪЯСНЕНИЕ ИИ",
    "context": "Контекст",
    "context_description": "Дополнительное пожелание к стандартному объяснению ИИ",
    "initial_help": "Выберите объект и нажмите «Получить модификаторы»",
    "object_mode_required": "Перейдите в режим объекта для анализа модификаторов.",
    "active_object_required": "Выберите активный объект для анализа модификаторов.",
    "modifiers_unsupported": "Активный объект не поддерживает модификаторы.",
    "file_changed": "Файл изменён; снова нажмите «Получить модификаторы»",
    "modifiers_found": "Найдено модификаторов: {count}. Объект: {object_name}",
    "no_modifiers": "У объекта {object_name} нет модификаторов",
    "choose_modifier_first": "Сначала получите модификаторы и выберите один из них.",
    "stale_selection": "Выбор модификатора устарел. Снова получите модификаторы.",
    "cleanup_failed": "Не удалось удалить временные данные {details}",
    "compare_failed": "Не удалось сравнить {modifier_type} с новым модификатором Blender: {details}",
    "compare_first": "Сначала сравните параметры выбранного модификатора.",
    "no_changed_parameters": "Изменённых параметров нет",
    "matches_defaults": "Совпадает со значениями нового модификатора Blender",
    "skipped": "Пропущено",
    "explanation_help": "Попросите Codex объяснить найденные изменения",
    "feature_unavailable": "Анализ модификаторов недоступен. Обновите add-on Astro Modeler.",
    "diff_read": "Детерминированный diff модификатора прочитан из Blender.",
}


def format_display_value(value):
    """Human-readable UI formatting; comparison/result values stay untouched."""
    if type(value) is float:
        try:
            blender_bits = struct.unpack(">I", struct.pack(">f", value))[0]
        except (OverflowError, struct.error):
            return format(value, ".8g")
        for significant_digits in range(1, 10):
            text = format(value, f".{significant_digits}g")
            try:
                displayed_bits = struct.unpack(">I", struct.pack(">f", float(text)))[0]
                # One float32 ULP is presentation noise at Blender's stored
                # precision; raw values and exact comparison remain untouched.
                if abs(displayed_bits - blender_bits) <= 1:
                    return text
            except (OverflowError, struct.error, ValueError):
                continue
        return format(value, ".9g")
    if isinstance(value, list):
        return "[" + ", ".join(format_display_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return ", ".join(f"{key}: {format_display_value(item)}" for key, item in value.items())
    return str(value)


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
