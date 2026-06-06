from typing import Any


def _get_var(data: dict, path: str) -> Any:
    parts = path.split(".")
    cur: Any = data
    for p in parts:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def evaluate_condition(condition: Any, data: dict[str, Any]) -> Any:
    """Minimal JSON Logic subset: ==, var, missing, and, or."""
    if condition is None or not isinstance(condition, dict):
        return condition
    if not condition:
        return False
    if "==" in condition:
        args = condition["=="]
        return evaluate_condition(args[0], data) == evaluate_condition(args[1], data)
    if "var" in condition:
        key = condition["var"]
        if isinstance(key, list):
            key = key[0]
        return _get_var(data, key)
    if "missing" in condition:
        fields = condition["missing"]
        for field in fields:
            val = _get_var(data, field.replace("extraction.", "extraction."))
            if val is None:
                return True
        return False
    if "and" in condition:
        return all(evaluate_condition(c, data) for c in condition["and"])
    if "or" in condition:
        return any(evaluate_condition(c, data) for c in condition["or"])
    if "!" in condition:
        return not evaluate_condition(condition["!"], data)
    return bool(condition)


def build_rule_context(
    extraction: dict[str, Any] | None,
    fraud: dict[str, Any] | None,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "extraction": extraction or {},
        "fraud": fraud or {},
        "metadata": metadata,
    }


def apply_rules(rules: list[dict], context: dict[str, Any]) -> dict[str, Any]:
    """Evaluate enabled rules in priority order; merge actions."""
    result: dict[str, Any] = {"applied_rules": [], "actions": {}}
    sorted_rules = sorted(rules, key=lambda r: r.get("priority", 100))
    for rule in sorted_rules:
        if not rule.get("enabled", True):
            continue
        condition = rule.get("condition_json", {})
        if evaluate_condition(condition, context):
            result["applied_rules"].append(rule.get("name"))
            action = rule.get("action_json", {})
            if isinstance(action, dict) and "set" in action:
                result["actions"].update(action["set"])
            elif isinstance(action, dict):
                result["actions"].update(action)
    return result
