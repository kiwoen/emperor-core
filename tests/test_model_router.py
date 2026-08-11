"""SmartRouter tests — P0.4 路由真实化.

Covers:
    - Capability / ModelTier enum coercion
    - classify(): keyword + pattern + domain fallback, never returns None
    - classify_domain(): minister domain → capability mapping
    - get_tier_for_capability(): capability → tier
    - get_fallback_chain_for_tier(): tier → ordered model chain
    - explain(): evidence for a classification decision
    - Config loading: missing / malformed YAML degrades to defaults
"""

import pytest

from jarvis.model_router import (
    Capability,
    ModelTier,
    SmartRouter,
)


@pytest.fixture
def router() -> SmartRouter:
    """A router with built-in defaults (no external config)."""
    return SmartRouter()


# ══════════════════════════════════════════════════════════════════
# Enums
# ══════════════════════════════════════════════════════════════════


class TestCapabilityEnum:
    def test_members_are_stable(self):
        assert Capability.MATH.value == "math"
        assert Capability.CODE.value == "code"
        assert Capability.REASON.value == "reason"
        assert Capability.RETRIEVE.value == "retrieve"
        assert Capability.UNKNOWN.value == "unknown"

    def test_coerce_from_string(self):
        assert Capability.coerce("math") is Capability.MATH
        assert Capability.coerce("CODE") is Capability.CODE

    def test_coerce_from_member(self):
        assert Capability.coerce(Capability.REASON) is Capability.REASON

    def test_coerce_unknown_never_raises(self):
        assert Capability.coerce("nonsense") is Capability.UNKNOWN
        assert Capability.coerce(None) is Capability.UNKNOWN
        assert Capability.coerce(object()) is Capability.UNKNOWN


class TestModelTierEnum:
    def test_members(self):
        assert ModelTier.PREMIUM.value == "premium"
        assert ModelTier.STANDARD.value == "standard"
        assert ModelTier.ECONOMY.value == "economy"

    def test_coerce_falls_back_to_standard(self):
        assert ModelTier.coerce("premium") is ModelTier.PREMIUM
        assert ModelTier.coerce("garbage") is ModelTier.STANDARD
        assert ModelTier.coerce(None) is ModelTier.STANDARD


# ══════════════════════════════════════════════════════════════════
# classify()
# ══════════════════════════════════════════════════════════════════


class TestClassify:
    @pytest.mark.parametrize(
        "prompt,expected",
        [
            ("What is 17 * 23?", Capability.MATH),
            ("计算 3 的平方根", Capability.MATH),
            ("Write a Python function to reverse a list", Capability.CODE),
            ("帮我调试这段代码，有个 bug", Capability.CODE),
            ("Why did the Roman empire collapse? Explain step by step.",
             Capability.REASON),
            ("请分析一下这个方案的利弊并推理原因", Capability.REASON),
            ("Search the web for the latest release notes", Capability.RETRIEVE),
            ("查一下最新的文档资料", Capability.RETRIEVE),
        ],
    )
    def test_keyword_classification(self, router, prompt, expected):
        assert router.classify(prompt) is expected

    def test_never_returns_none(self, router):
        """classify() must always yield a Capability — worst case UNKNOWN."""
        for prompt in ["", "   ", "hmm", "asdfghjkl", "。。。"]:
            result = router.classify(prompt)
            assert isinstance(result, Capability)

    def test_unrecognised_prompt_is_unknown(self, router):
        assert router.classify("asdfghjkl qwertyuiop") is Capability.UNKNOWN

    def test_domain_used_when_prompt_is_uninformative(self, router):
        """A meaningless prompt still routes via the declared domain."""
        assert router.classify("...", domain="code") is Capability.CODE
        assert router.classify("...", domain="math") is Capability.MATH

    def test_prompt_signal_beats_domain(self, router):
        """An explicit prompt signal outranks a generic domain hint."""
        assert router.classify(
            "write a python function that sorts a list", domain="general"
        ) is Capability.CODE

    def test_non_string_prompt_is_tolerated(self, router):
        assert isinstance(router.classify(None), Capability)  # type: ignore[arg-type]
        assert isinstance(router.classify(12345), Capability)  # type: ignore[arg-type]


class TestExplain:
    def test_explain_returns_evidence(self, router):
        info = router.explain("What is 17 * 23?")
        assert info["capability"] == Capability.MATH.value
        assert "scores" in info
        assert "reason" in info
        assert info["reason"]

    def test_explain_on_unknown(self, router):
        info = router.explain("asdfghjkl")
        assert info["capability"] == Capability.UNKNOWN.value


# ══════════════════════════════════════════════════════════════════
# classify_domain()
# ══════════════════════════════════════════════════════════════════


class TestClassifyDomain:
    @pytest.mark.parametrize(
        "domain,expected",
        [
            ("math", Capability.MATH),
            ("science", Capability.MATH),
            ("code", Capability.CODE),
            ("engineering", Capability.CODE),
            ("search", Capability.RETRIEVE),
            ("writing", Capability.REASON),
        ],
    )
    def test_known_domains(self, router, domain, expected):
        assert router.classify_domain(domain) is expected

    def test_unknown_domain(self, router):
        assert router.classify_domain("underwater-basket-weaving") is (
            Capability.UNKNOWN
        )

    def test_case_and_whitespace_insensitive(self, router):
        assert router.classify_domain("  CODE  ") is Capability.CODE

    def test_empty_domain(self, router):
        assert router.classify_domain("") is Capability.UNKNOWN
        assert router.classify_domain(None) is Capability.UNKNOWN  # type: ignore


# ══════════════════════════════════════════════════════════════════
# Tier + fallback chain
# ══════════════════════════════════════════════════════════════════


class TestTierMapping:
    def test_every_capability_has_a_tier(self, router):
        for cap in Capability:
            tier = router.get_tier_for_capability(cap)
            assert isinstance(tier, str) and tier
            # tier must be a valid ModelTier value
            assert tier in {t.value for t in ModelTier}

    def test_hard_capabilities_get_premium(self, router):
        assert router.get_tier_for_capability(Capability.MATH) == "premium"
        assert router.get_tier_for_capability(Capability.CODE) == "premium"

    def test_retrieval_is_cheap(self, router):
        assert router.get_tier_for_capability(Capability.RETRIEVE) == "economy"

    def test_accepts_raw_string(self, router):
        assert router.get_tier_for_capability("math") == "premium"

    def test_unknown_capability_gets_standard(self, router):
        assert router.get_tier_for_capability(Capability.UNKNOWN) == "standard"
        assert router.get_tier_for_capability("nonsense") == "standard"


class TestFallbackChain:
    def test_every_tier_has_a_non_empty_chain(self, router):
        for tier in ModelTier:
            chain = router.get_fallback_chain_for_tier(tier)
            assert isinstance(chain, list)
            assert len(chain) >= 1
            assert all(isinstance(m, str) and m for m in chain)

    def test_chain_has_no_duplicates(self, router):
        for tier in ModelTier:
            chain = router.get_fallback_chain_for_tier(tier)
            assert len(chain) == len(set(chain)), f"{tier} chain has duplicates"

    def test_accepts_raw_string_tier(self, router):
        assert router.get_fallback_chain_for_tier("premium") == (
            router.get_fallback_chain_for_tier(ModelTier.PREMIUM)
        )

    def test_unknown_tier_falls_back_to_standard(self, router):
        assert router.get_fallback_chain_for_tier("nonsense") == (
            router.get_fallback_chain_for_tier(ModelTier.STANDARD)
        )

    def test_returned_chain_is_a_copy(self, router):
        """Mutating the returned list must not corrupt router state."""
        chain = router.get_fallback_chain_for_tier(ModelTier.PREMIUM)
        chain.append("injected-model")
        assert "injected-model" not in router.get_fallback_chain_for_tier(
            ModelTier.PREMIUM
        )


# ══════════════════════════════════════════════════════════════════
# route() — the end-to-end decision
# ══════════════════════════════════════════════════════════════════


class TestRoute:
    def test_route_returns_full_decision(self, router):
        decision = router.route("What is 17 * 23?", domain="math")
        assert decision["capability"] == Capability.MATH.value
        assert decision["tier"] == "premium"
        assert isinstance(decision["fallback_chain"], list)
        assert decision["fallback_chain"]
        assert decision["model"] == decision["fallback_chain"][0]

    def test_route_on_gibberish_still_produces_a_model(self, router):
        decision = router.route("asdfghjkl")
        assert decision["capability"] == Capability.UNKNOWN.value
        assert decision["model"]


# ══════════════════════════════════════════════════════════════════
# Config loading — degradation
# ══════════════════════════════════════════════════════════════════


class TestConfigLoading:
    def test_missing_config_uses_defaults(self, tmp_path):
        missing = tmp_path / "nope.yaml"
        r = SmartRouter(config_path=str(missing))
        # Still fully functional on built-in defaults
        assert r.classify("What is 2 + 2?") is Capability.MATH
        assert r.get_fallback_chain_for_tier(ModelTier.PREMIUM)

    def test_malformed_config_uses_defaults(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("::: not : valid : yaml :::\n  - [", encoding="utf-8")
        r = SmartRouter(config_path=str(bad))
        assert r.classify("What is 2 + 2?") is Capability.MATH

    def test_describe_is_serialisable(self, router):
        info = router.describe()
        assert "tiers" in info
        assert "capabilities" in info
        assert isinstance(info["capabilities"], list)
