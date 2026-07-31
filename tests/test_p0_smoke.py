"""Smoke test script for P0.1 + P0.2 modules."""
import sys
from jarvis.governance_agent import (
    GovernanceAgent, GovernanceRule, GovernanceResult, GovernanceStatus, RulePriority,
)
from jarvis.bounded_autonomy import (
    ActionZone, ActionSpace, BoundedAutonomyEngine, BoundedAutonomyResult,
)

def test_governance_agent():
    """Test GovernanceAgent: rules CRUD + validation."""
    gov = GovernanceAgent()
    
    # Rule registration
    rule = GovernanceAgent.make_policy_rule(
        'test-rule',
        lambda action, ctx: 'bad' not in str(action),
        priority=RulePriority.CRITICAL,
        description='Block bad actions',
    )
    gov.register_rule(rule)
    assert gov.get_rule('test-rule') is not None
    
    # Validation — pass
    result = gov.validate(action={'tool': 'read'}, context={'domain': 'test'})
    assert result.passed
    assert result.status == GovernanceStatus.PASSED
    print(f'  PASS: status={result.status}, matched_rules={result.matched_rules}')
    
    # Validation — block (CRITICAL rule fails)
    result = gov.validate(action={'tool': 'bad_delete'}, context={'domain': 'test'})
    assert result.blocked
    assert result.status == GovernanceStatus.BLOCKED
    assert 'test-rule' in result.failed_rules
    print(f'  BLOCK: status={result.status}, failed_rules={result.failed_rules}')
    
    # Disable rule → passes again
    gov.disable_rule('test-rule')
    result = gov.validate(action={'tool': 'bad_delete'}, context={'domain': 'test'})
    assert result.passed
    print(f'  AFTER DISABLE: passed={result.passed}')
    
    # Re-enable → blocks again
    gov.enable_rule('test-rule')
    result = gov.validate(action={'tool': 'bad_delete'}, context={'domain': 'test'})
    assert result.blocked
    print(f'  AFTER RE-ENABLE: blocked={result.blocked}')
    
    # Deregister
    gov.deregister_rule('test-rule')
    assert gov.get_rule('test-rule') is None
    print(f'  DEREGISTER OK: rule absent={gov.get_rule("test-rule") is None}')
    
    # Rule types
    rbac = GovernanceAgent.make_rbac_rule('rbac-test', lambda a, c: True)
    reg = GovernanceAgent.make_regulatory_rule('reg-test', lambda a, c: True)
    biz = GovernanceAgent.make_business_rule('biz-test', lambda a, c: True)
    assert rbac.rule_type == 'rbac'
    assert reg.rule_type == 'regulatory'
    assert biz.rule_type == 'business_logic'
    print(f'  Rule types OK: rbac={rbac.rule_type}, regulatory={reg.rule_type}, business={biz.rule_type}')
    
    # List rules
    gov.register_rule(rbac)
    gov.register_rule(reg)
    gov.register_rule(biz)
    rules = gov.list_rules()
    print(f'  List rules: {len(rules)} total')
    rules_policy = gov.list_rules(rule_type='rbac')
    print(f'  Filter rbac: {len(rules_policy)} rules')
    assert len(rules_policy) == 1
    
    # Priority ordering test
    gov2 = GovernanceAgent()
    gov2.register_rule(GovernanceAgent.make_policy_rule('low', lambda a, c: True, priority=RulePriority.LOW))
    gov2.register_rule(GovernanceAgent.make_policy_rule('high', lambda a, c: True, priority=RulePriority.HIGH))
    gov2.register_rule(GovernanceAgent.make_policy_rule('critical', lambda a, c: True, priority=RulePriority.CRITICAL))
    ordered = gov2.list_rules()
    assert ordered[0].name == 'critical'
    assert ordered[1].name == 'high'
    assert ordered[2].name == 'low'
    print(f'  Priority order: {[r.name for r in ordered]}')
    
    print('  >>> GovernanceAgent tests PASSED <<<')
    assert True  # all assertions passed


def test_bounded_autonomy():
    """Test BoundedAutonomyEngine: zone classification + space management."""
    engine = BoundedAutonomyEngine(load_defaults=True)
    
    # GREEN: read-only
    zone = engine.classify({'tool': 'read', 'prompt': 'list files'}, {'domain': 'general'})
    assert zone == ActionZone.GREEN, f'Expected GREEN, got {zone}'
    print(f'  read -> {zone}')
    
    # YELLOW: modify
    zone = engine.classify({'tool': 'modify', 'prompt': 'update config'}, {'domain': 'config'})
    assert zone == ActionZone.YELLOW, f'Expected YELLOW, got {zone}'
    print(f'  modify -> {zone}')
    
    # RED: delete
    zone = engine.classify({'tool': 'delete', 'prompt': 'delete files'}, {'domain': 'general'})
    assert zone == ActionZone.RED, f'Expected RED, got {zone}'
    print(f'  delete -> {zone}')
    
    # RED: payment
    zone = engine.classify({'tool': 'refund', 'prompt': 'process refund'}, {'domain': 'finance'})
    assert zone == ActionZone.RED, f'Expected RED, got {zone}'
    print(f'  refund -> {zone}')
    
    # Custom space registration
    engine.register_space(ActionSpace(
        name='custom-green',
        zone=ActionZone.GREEN,
        keywords={'custom_safe_op'},
        priority=200,
    ))
    zone = engine.classify({'tool': 'custom_tool', 'prompt': 'do custom_safe_op now'})
    assert zone == ActionZone.GREEN, f'Expected GREEN, got {zone}'
    print(f'  custom_safe_op -> {zone}')
    
    # Deregister
    engine.deregister_space('custom-green')
    assert engine.get_space('custom-green') is None
    print(f'  Deregister custom space OK')
    
    # List spaces
    greens = engine.list_spaces(ActionZone.GREEN)
    yellows = engine.list_spaces(ActionZone.YELLOW)
    reds = engine.list_spaces(ActionZone.RED)
    print(f'  Spaces: {len(greens)} GREEN, {len(yellows)} YELLOW, {len(reds)} RED')
    
    # Default zone (no match → YELLOW)
    engine2 = BoundedAutonomyEngine(load_defaults=False)
    zone = engine2.classify({'tool': 'unknown_thing'})
    assert zone == ActionZone.YELLOW  # default
    print(f'  Default zone -> {zone}')
    
    # evaluate() without approval engine — YELLOW
    result = engine2.evaluate({'tool': 'modify'}, {'domain': 'config'})
    assert result.zone == ActionZone.YELLOW
    assert not result.can_proceed
    assert result.needs_approval
    print(f'  evaluate(YELLOW): can_proceed={result.can_proceed}, needs_approval={result.needs_approval}')
    
    # evaluate() GREEN
    result = engine.evaluate({'tool': 'read', 'prompt': 'status'}, {'domain': 'general'})
    assert result.zone == ActionZone.GREEN
    assert result.can_proceed
    print(f'  evaluate(GREEN): can_proceed={result.can_proceed}')
    
    # ActionSpace to_dict
    space = ActionSpace(name='test', zone=ActionZone.GREEN, keywords={'kw1', 'kw2'})
    d = space.to_dict()
    assert d['name'] == 'test'
    assert d['zone'] == 'green'
    print(f'  ActionSpace.to_dict OK: {d}')
    
    # BoundedAutonomyResult to_dict
    r = BoundedAutonomyResult(zone=ActionZone.RED, reason='test', task_id='t1')
    d = r.to_dict()
    assert d['zone'] == 'red'
    print(f'  BoundedAutonomyResult.to_dict OK')
    
    print('  >>> BoundedAutonomyEngine tests PASSED <<<')
    assert True  # all assertions passed


def test_integration():
    """Test GovernanceAgent + BoundedAutonomyEngine integration."""
    gov = GovernanceAgent()
    gov.register_rule(GovernanceAgent.make_policy_rule(
        'no-delete',
        lambda action, ctx: 'delete' not in str(action).lower(),
        priority=RulePriority.CRITICAL,
        description='No deletion allowed',
    ))
    
    engine = BoundedAutonomyEngine(
        governance_agent=gov,
        load_defaults=False,
    )
    # Register a space that would normally be RED for deletes
    engine.register_space(ActionSpace(
        name='destructive', zone=ActionZone.RED,
        keywords={'delete', 'remove', 'destroy'},
        priority=100,
    ))
    
    # RED action → goes through governance
    result = engine.evaluate(
        {'tool': 'delete', 'prompt': 'delete all logs'},
        {'domain': 'system'},
    )
    assert result.zone == ActionZone.RED
    assert not result.can_proceed
    assert result.governance_result is not None
    assert result.governance_result.blocked
    print(f'  RED+Gov: zone={result.zone}, blocked={result.governance_result.blocked}')
    print(f'  Reason: {result.reason}')
    
    # GREEN action → no governance
    result = engine.evaluate(
        {'tool': 'read', 'prompt': 'read config'},
        {'domain': 'system'},
    )
    assert result.zone == ActionZone.YELLOW  # default zone since no matching space
    print(f'  Default/no-match: zone={result.zone}')
    
    print('  >>> Integration tests PASSED <<<')
    assert True  # all assertions passed


if __name__ == '__main__':
    all_ok = True
    try:
        test_governance_agent()
    except Exception as e:
        print(f'FAIL: GovernanceAgent - {e}')
        import traceback; traceback.print_exc()
        all_ok = False
    
    try:
        test_bounded_autonomy()
    except Exception as e:
        print(f'FAIL: BoundedAutonomy - {e}')
        import traceback; traceback.print_exc()
        all_ok = False
    
    try:
        test_integration()
    except Exception as e:
        print(f'FAIL: Integration - {e}')
        import traceback; traceback.print_exc()
        all_ok = False
    
    if all_ok:
        print('\n=== ALL SMOKE TESTS PASSED ===')
        sys.exit(0)
    else:
        print('\n=== SOME TESTS FAILED ===')
        sys.exit(1)
