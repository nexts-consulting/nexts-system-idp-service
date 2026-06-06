from idp_app.rules_engine import apply_rules, build_rule_context


def test_block_fraud_rule():
    rules = [
        {
            "name": "block_fraud",
            "priority": 10,
            "enabled": True,
            "condition_json": {"==": [{"var": "fraud.predicted_tampered"}, True]},
            "action_json": {"set": {"blocked": True}},
        }
    ]
    ctx = build_rule_context({}, {"predicted_tampered": True}, {})
    result = apply_rules(rules, ctx)
    assert result["actions"]["blocked"] is True
