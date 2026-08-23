"""
Tests for P0.5 Three-Tier Guard Enhancement (jarvis.tool_guard tiers).

Covers:
  - ToolActionClassifier: read/write/external classification
  - ToolRiskLevel: low/medium/high/critical mapping
  - RoleScopedAccess: admin/standard/viewer access control
  - ThreeTierGuardEnhancement: integrated three-tier pipeline
  - ToolGuardMiddleware: tiers integration in wrap_tool_call
  - ConfirmationStrategy: auto/check/confirm/block routing
"""

import sys
import os

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from jarvis.tool_guard import (
    ToolActionType,
    ToolRiskLevel,
    AgentRole,
    ConfirmationStrategy,
    ToolActionClassifier,
    RoleScopedAccess,
    ThreeTierGuardEnhancement,
    ActionClassification,
    AccessCheckResult,
    ToolGuardMiddleware,
    RateLimiter,
)


# ═══════════════════════════════════════════════════════════════════
# Test 1: ToolActionClassifier — read classification
# ═══════════════════════════════════════════════════════════════════

class TestActionClassification:
    """Test ToolActionClassifier for read/write/external classification."""

    def test_classify_read_tool_explicit(self):
        """Explicitly registered read tool → READ + LOW risk."""
        c = ToolActionClassifier(
            tool_action_registry={"read_file": "read"}
        )
        result = c.classify("read_file")
        assert result.action_type == ToolActionType.READ
        assert result.risk_level == ToolRiskLevel.LOW
        assert result.matched_by == "registry"

    def test_classify_write_tool_explicit(self):
        """Explicitly registered write tool → WRITE + MEDIUM risk."""
        c = ToolActionClassifier(
            tool_action_registry={"write_file": "write"}
        )
        result = c.classify("write_file")
        assert result.action_type == ToolActionType.WRITE
        assert result.risk_level == ToolRiskLevel.MEDIUM
        assert result.matched_by == "registry"

    def test_classify_external_tool_explicit(self):
        """Explicitly registered external tool → EXTERNAL + HIGH risk."""
        c = ToolActionClassifier(
            tool_action_registry={"send_message": "external"}
        )
        result = c.classify("send_message")
        assert result.action_type == ToolActionType.EXTERNAL
        assert result.risk_level == ToolRiskLevel.HIGH
        assert result.matched_by == "registry"

    def test_classify_hint_based_read(self):
        """No explicit registration → heuristic matches 'search' → READ."""
        c = ToolActionClassifier()
        result = c.classify("search_files")
        assert result.action_type == ToolActionType.READ
        assert result.matched_by == "hint"

    def test_classify_hint_based_write(self):
        """No explicit registration → heuristic matches 'delete' → WRITE."""
        c = ToolActionClassifier()
        result = c.classify("delete_file")
        assert result.action_type == ToolActionType.WRITE
        assert result.matched_by == "hint"

    def test_classify_hint_based_external(self):
        """No explicit registration → heuristic matches 'send' → EXTERNAL."""
        c = ToolActionClassifier()
        result = c.classify("send_notification")
        assert result.action_type == ToolActionType.EXTERNAL
        assert result.matched_by == "hint"

    def test_classify_fallback_default(self):
        """No match at all → fallback to default (write)."""
        c = ToolActionClassifier(fallback_action_type="write")
        result = c.classify("unknown_xyz_tool")
        assert result.action_type == ToolActionType.WRITE
        assert result.matched_by == "default"

    def test_classify_external_hint_priority(self):
        """External hints take priority over write hints."""
        c = ToolActionClassifier()
        # "send_delete" contains both "send" (external) and "delete" (write)
        # external hints should be checked first
        result = c.classify("send_delete_operation")
        assert result.action_type == ToolActionType.EXTERNAL

    def test_dynamic_register(self):
        """Dynamic registration after init should override default."""
        c = ToolActionClassifier()
        c.register("custom_tool", "read")
        result = c.classify("custom_tool")
        assert result.action_type == ToolActionType.READ
        assert result.matched_by == "registry"


# ═══════════════════════════════════════════════════════════════════
# Test 2: RoleScopedAccess — access control
# ═══════════════════════════════════════════════════════════════════

class TestRoleScopedAccess:
    """Test RoleScopedAccess for admin/standard/viewer boundaries."""

    def test_admin_access_all(self):
        """Admin can access READ, WRITE, and EXTERNAL tools."""
        rsa = RoleScopedAccess()
        for at in [ToolActionType.READ, ToolActionType.WRITE, ToolActionType.EXTERNAL]:
            result = rsa.check_access(AgentRole.ADMIN, f"tool_{at.value}", at)
            assert result.allowed, f"Admin should access {at.value}"

    def test_standard_read_write_allowed(self):
        """Standard role can access READ and WRITE."""
        rsa = RoleScopedAccess()
        assert rsa.check_access(AgentRole.STANDARD, "read_file", ToolActionType.READ).allowed
        assert rsa.check_access(AgentRole.STANDARD, "write_file", ToolActionType.WRITE).allowed

    def test_standard_external_blocked(self):
        """Standard role cannot access EXTERNAL tools."""
        rsa = RoleScopedAccess()
        result = rsa.check_access(AgentRole.STANDARD, "send_message", ToolActionType.EXTERNAL)
        assert not result.allowed
        assert result.confirmation_strategy == ConfirmationStrategy.BLOCK
        assert "standard" in result.block_reason
        assert "EXTERNAL" in result.block_reason

    def test_viewer_read_only(self):
        """Viewer role can only access READ tools."""
        rsa = RoleScopedAccess()
        assert rsa.check_access(AgentRole.VIEWER, "read_file", ToolActionType.READ).allowed

    def test_viewer_write_blocked(self):
        """Viewer role cannot access WRITE tools."""
        rsa = RoleScopedAccess()
        result = rsa.check_access(AgentRole.VIEWER, "write_file", ToolActionType.WRITE)
        assert not result.allowed
        assert result.confirmation_strategy == ConfirmationStrategy.BLOCK
        assert "READ-only" in result.block_reason

    def test_viewer_external_blocked(self):
        """Viewer role cannot access EXTERNAL tools."""
        rsa = RoleScopedAccess()
        result = rsa.check_access(AgentRole.VIEWER, "send_message", ToolActionType.EXTERNAL)
        assert not result.allowed
        assert result.confirmation_strategy == ConfirmationStrategy.BLOCK


# ═══════════════════════════════════════════════════════════════════
# Test 3: ConfirmationStrategy routing
# ═══════════════════════════════════════════════════════════════════

class TestConfirmationStrategy:
    """Test confirmation strategy routing per action type."""

    def test_read_auto_confirm(self):
        """READ action → AUTO confirmation (auto-allow)."""
        rsa = RoleScopedAccess()
        result = rsa.check_access(AgentRole.STANDARD, "read_file", ToolActionType.READ)
        assert result.allowed
        assert result.confirmation_strategy == ConfirmationStrategy.AUTO

    def test_write_check_rules(self):
        """WRITE action → CHECK confirmation (check business rules)."""
        rsa = RoleScopedAccess()
        result = rsa.check_access(AgentRole.STANDARD, "write_file", ToolActionType.WRITE)
        assert result.allowed
        assert result.confirmation_strategy == ConfirmationStrategy.CHECK

    def test_external_confirm_required(self):
        """EXTERNAL action → CONFIRM confirmation (explicit user consent)."""
        rsa = RoleScopedAccess()
        result = rsa.check_access(AgentRole.ADMIN, "send_message", ToolActionType.EXTERNAL)
        assert result.allowed
        assert result.confirmation_strategy == ConfirmationStrategy.CONFIRM


# ═══════════════════════════════════════════════════════════════════
# Test 4: ThreeTierGuardEnhancement integration
# ═══════════════════════════════════════════════════════════════════

class TestThreeTierEnhancement:
    """Test full three-tier pipeline: classify → risk → role access."""

    def setup_method(self):
        self.tiers = ThreeTierGuardEnhancement(
            tool_registry={
                "read_file": "read",
                "write_file": "write",
                "send_message": "external",
            },
            default_role=AgentRole.STANDARD,
        )

    def test_evaluate_read_allowed_auto(self):
        """Read tool with standard role → allowed + AUTO strategy."""
        result = self.tiers.evaluate("read_file")
        assert result.allowed
        assert result.confirmation_strategy == ConfirmationStrategy.AUTO
        assert result.action_type == ToolActionType.READ

    def test_evaluate_write_allowed_check(self):
        """Write tool with standard role → allowed + CHECK strategy."""
        result = self.tiers.evaluate("write_file")
        assert result.allowed
        assert result.confirmation_strategy == ConfirmationStrategy.CHECK
        assert result.action_type == ToolActionType.WRITE

    def test_evaluate_external_blocked_standard(self):
        """External tool with standard role → BLOCKED."""
        result = self.tiers.evaluate("send_message")
        assert not result.allowed
        assert result.confirmation_strategy == ConfirmationStrategy.BLOCK

    def test_evaluate_external_allowed_admin(self):
        """External tool with admin role → allowed + CONFIRM strategy."""
        result = self.tiers.evaluate("send_message", role=AgentRole.ADMIN)
        assert result.allowed
        assert result.confirmation_strategy == ConfirmationStrategy.CONFIRM

    def test_evaluate_viewer_write_blocked(self):
        """Write tool with viewer role → BLOCKED."""
        result = self.tiers.evaluate("write_file", role=AgentRole.VIEWER)
        assert not result.allowed
        assert result.confirmation_strategy == ConfirmationStrategy.BLOCK
        assert "READ-only" in result.block_reason

    def test_classify_only(self):
        """classify_only returns ActionClassification without role check."""
        result = self.tiers.classify_only("send_message")
        assert isinstance(result, ActionClassification)
        assert result.action_type == ToolActionType.EXTERNAL
        assert result.risk_level == ToolRiskLevel.HIGH

    def test_register_tool_dynamic(self):
        """Dynamic registration updates the classifier."""
        self.tiers.register_tool("new_payment_tool", "external")
        result = self.tiers.evaluate("new_payment_tool", role=AgentRole.ADMIN)
        assert result.allowed
        assert result.action_type == ToolActionType.EXTERNAL
        assert result.confirmation_strategy == ConfirmationStrategy.CONFIRM


# ═══════════════════════════════════════════════════════════════════
# Test 5: ToolGuardMiddleware + Tiers integration
# ═══════════════════════════════════════════════════════════════════

class TestToolGuardMiddlewareWithTiers:
    """Test ToolGuardMiddleware with ThreeTierGuardEnhancement enabled."""

    def setup_method(self):
        self.tiers = ThreeTierGuardEnhancement(
            tool_registry={
                "read_file": "read",
                "write_file": "write",
                "send_message": "external",
            },
            default_role=AgentRole.STANDARD,
        )

    def test_tiers_blocks_external_for_standard(self):
        """Middleware + tiers: standard role blocked from external tool."""
        guard = ToolGuardMiddleware(tiers=self.tiers)

        def send_msg(text):
            return f"sent: {text}"

        wrapped = guard.wrap_tool_call("send_message", send_msg)
        result = wrapped("hello")
        assert not result.success
        assert result.blocked
        assert result.block_reason == "tier_access_denied"

    def test_tiers_allows_read_for_standard(self):
        """Middleware + tiers: standard role allowed read with auto strategy."""
        guard = ToolGuardMiddleware(tiers=self.tiers)

        def read_file(path):
            return f"content of {path}"

        wrapped = guard.wrap_tool_call("read_file", read_file)
        result = wrapped("/tmp/doc.txt")
        assert result.success
        assert "content of /tmp/doc.txt" in str(result.output)

    def test_admin_full_access(self):
        """Middleware + tiers: admin role can access all tools."""
        admin_tiers = ThreeTierGuardEnhancement(
            tool_registry={
                "read_file": "read",
                "write_file": "write",
                "send_message": "external",
            },
            default_role=AgentRole.ADMIN,
        )
        guard = ToolGuardMiddleware(tiers=admin_tiers)

        # All three should succeed
        wrapped_read = guard.wrap_tool_call("read_file", lambda path: f"read:{path}")
        wrapped_write = guard.wrap_tool_call("write_file", lambda path: f"write:{path}")
        wrapped_send = guard.wrap_tool_call("send_message", lambda text: f"sent:{text}")

        assert wrapped_read("/x").success
        assert wrapped_write("/y").success
        assert wrapped_send("hi").success

    def test_no_tiers_passthrough(self):
        """Middleware without tiers: original behavior unchanged."""
        guard = ToolGuardMiddleware()  # no tiers
        wrapped = guard.wrap_tool_call("send_message", lambda text: f"sent:{text}")
        result = wrapped("hello")
        assert result.success
        # No tiers = no tier-level blocking, but input validation / rate-limit still apply
        assert "sent:hello" in str(result.output)


# ═══════════════════════════════════════════════════════════════════
# Test 6: Edge cases
# ═══════════════════════════════════════════════════════════════════

class TestTiersEdgeCases:
    """Edge cases for the three-tier guard system."""

    def test_wildcard_registry(self):
        """Wildcard registration 'send_*' → EXTERNAL for matching tools."""
        tiers = ThreeTierGuardEnhancement(
            tool_registry={"send_*": "external"},
            default_role=AgentRole.ADMIN,
        )
        result = tiers.evaluate("send_email")
        assert result.action_type == ToolActionType.EXTERNAL

        result2 = tiers.evaluate("send_sms")
        assert result2.action_type == ToolActionType.EXTERNAL

    def test_access_result_contains_all_fields(self):
        """AccessCheckResult has all expected fields populated."""
        rsa = RoleScopedAccess()
        result = rsa.check_access(AgentRole.VIEWER, "write_file", ToolActionType.WRITE)
        assert result.role == AgentRole.VIEWER
        assert result.tool_name == "write_file"
        assert result.action_type == ToolActionType.WRITE
        assert not result.allowed
        assert result.block_reason
        assert result.confirmation_strategy == ConfirmationStrategy.BLOCK

    def test_classification_read_via_list_hint(self):
        """'list' hint → READ classification."""
        c = ToolActionClassifier()
        result = c.classify("list_users")
        assert result.action_type == ToolActionType.READ
        assert result.risk_level == ToolRiskLevel.LOW

    def test_classification_write_via_update_hint(self):
        """'update' hint → WRITE classification."""
        c = ToolActionClassifier()
        result = c.classify("update_profile")
        assert result.action_type == ToolActionType.WRITE
        assert result.risk_level == ToolRiskLevel.MEDIUM

    def test_classification_external_via_publish_hint(self):
        """'publish' hint → EXTERNAL classification."""
        c = ToolActionClassifier()
        result = c.classify("publish_article")
        assert result.action_type == ToolActionType.EXTERNAL
        assert result.risk_level == ToolRiskLevel.HIGH


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
