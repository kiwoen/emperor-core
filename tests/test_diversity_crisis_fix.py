"""Tests for the minister-homogeneity / diversity-crisis optimization.

Covers:
  * Pure re-dispersion helpers (genomic_diversity, redisperse, ARCHETYPES).
  * Diverse seeding: register_minister no longer produces byte-identical genomes.
  * Restart self-heal: a deployed homogeneous genome_state.json is re-dispersed
    on load via Court.redisperse_if_homogeneous.
  * Crisis gate: streak is driven by *genomic* diversity, so merit noise can no
    longer reset it and leave a homogeneous court stuck in perpetual crisis.
  * Bred-minister key mapping: AutoBreeder's abstract traits reach the real genes.
"""

import json
import tempfile
from pathlib import Path

from jarvis.court.court import Court
from jarvis.court.diversity import DiversityMonitor, DiversitySnapshot
from jarvis.court.evolution import MinisterGenome
from jarvis.court.genome_redispersal import (
    ARCHETYPES,
    archetype_for_index,
    genomic_diversity,
    redisperse,
)
from jarvis.court.breeding import BreedingStrategy


def _homogeneous_genomes(names):
    """Build a {name: MinisterGenome} dict where every minister is identical."""
    return {
        n: MinisterGenome(
            name=n, domain="general",
            temperature=0.7, confidence_baseline=0.85,
            exploration_rate=0.3, conservatism=0.5,
            prompt_mutation_rate=0.1, specialization_weight=1.0,
        )
        for n in names
    }


def _archetype_genomes(names):
    """Build a {name: MinisterGenome} dict spread across the archetypes."""
    return {
        n: MinisterGenome(
            name=n, domain="general",
            temperature=0.7, confidence_baseline=0.85,
            exploration_rate=archetype_for_index(i)["exploration_rate"],
            conservatism=archetype_for_index(i)["conservatism"],
            prompt_mutation_rate=archetype_for_index(i)["prompt_mutation_rate"],
            specialization_weight=archetype_for_index(i)["specialization_weight"],
        )
        for i, n in enumerate(names)
    }


class TestRedispersalHelpers:
    def test_genomic_diversity_homogeneous_is_zero(self):
        g = _homogeneous_genomes(["a", "b", "c", "d", "e", "f"])
        assert genomic_diversity(g) == 0.0

    def test_genomic_diversity_diverse_is_high(self):
        g = _archetype_genomes(["a", "b", "c", "d", "e", "f"])
        assert genomic_diversity(g) > 0.15

    def test_single_minister_returns_max(self):
        assert genomic_diversity({"solo": MinisterGenome("solo", "general")}) == 1.0

    def test_archetypes_pairwise_distinct(self):
        import math
        for i in range(len(ARCHETYPES)):
            for j in range(i + 1, len(ARCHETYPES)):
                a, b = ARCHETYPES[i], ARCHETYPES[j]
                keys = ("exploration_rate", "conservatism",
                        "prompt_mutation_rate", "specialization_weight")
                vec_a = [a[k] for k in keys]
                vec_b = [b[k] for k in keys]
                dot = sum(x * y for x, y in zip(vec_a, vec_b))
                na = math.sqrt(sum(x * x for x in vec_a))
                nb = math.sqrt(sum(y * y for y in vec_b))
                sim = dot / (na * nb)
                assert sim < 0.999, "archetypes %d,%d are collinear" % (i, j)

    def test_redisperse_raises_diversity(self):
        g = _homogeneous_genomes(["a", "b", "c", "d", "e", "f"])
        g, changed = redisperse(g, threshold=0.10)
        assert changed is True
        assert genomic_diversity(g) > 0.15

    def test_redisperse_idempotent_when_diverse(self):
        g = _archetype_genomes(["a", "b", "c", "d", "e", "f"])
        _, changed = redisperse(g, threshold=0.10)
        assert changed is False  # already diverse -> no-op

    def test_redisperse_is_deterministic(self):
        g1 = _homogeneous_genomes(["a", "b", "c"])
        g2 = _homogeneous_genomes(["a", "b", "c"])
        redisperse(g1)
        redisperse(g2)
        for n in g1:
            assert g1[n].exploration_rate == g2[n].exploration_rate
            assert g1[n].conservatism == g2[n].conservatism


class TestDiverseSeeding:
    def test_register_many_produces_diverse_population(self):
        court = Court()
        court.register_many([
            {"name": "m%d" % i, "domain": "general"} for i in range(6)
        ])
        div = genomic_diversity(court._sm._genomes)
        # With archetype rotation, 6 ministers must be clearly non-identical.
        assert div > 0.15

    def test_seeded_genes_are_not_all_defaults(self):
        court = Court()
        court.register_many([
            {"name": "x1", "domain": "general"},
            {"name": "x2", "domain": "general"},
        ])
        g1 = court._sm._genomes["x1"]
        g2 = court._sm._genomes["x2"]
        # At least one personality gene must differ between the two ministers.
        assert (g1.exploration_rate, g1.conservatism,
                g1.prompt_mutation_rate, g1.specialization_weight) != (
            g2.exploration_rate, g2.conservatism,
            g2.prompt_mutation_rate, g2.specialization_weight)


class TestRestartSelfHeal:
    def _write_homogeneous_state(self, tmp_path, names):
        payload = {
            "version": 1,
            "metadata": {},
            "genomes": [
                {
                    "name": n, "domain": "general",
                    "temperature": 0.7, "confidence_baseline": 0.85,
                    "exploration_rate": 0.3, "conservatism": 0.5,
                    "prompt_mutation_rate": 0.1, "specialization_weight": 1.0,
                    "generation": 0, "parent": "",
                }
                for n in names
            ],
        }
        p = tmp_path / "genome_state.json"
        p.write_text(json.dumps(payload))
        return str(p)

    def test_load_then_redisperse_cures_homogeneous_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._write_homogeneous_state(Path(d),
                                                 ["m%d" % i for i in range(6)])
            court = Court()
            court.load_genomes(path)
            # Loaded population is byte-identical -> diversity collapsed.
            assert genomic_diversity(court._sm._genomes) == 0.0
            changed = court.redisperse_if_homogeneous()
            assert changed is True
            assert genomic_diversity(court._sm._genomes) > 0.15

    def test_redisperse_noop_when_already_diverse(self):
        court = Court()
        court.register_many([{"name": "m%d" % i, "domain": "general"}
                             for i in range(6)])
        # Already diverse from seeding -> heal is a no-op.
        assert court.redisperse_if_homogeneous() is False


class TestLoadStateHealPersists:
    """Mirrors Emperor._load_state(): a deployed homogeneous genomes.json is
    healed on boot AND written back so the cure survives the next restart."""

    def _write_homogeneous_genomes(self, tmp_path, names):
        payload = {
            "version": 1,
            "metadata": {},
            "genomes": [
                {
                    "name": n, "domain": "general",
                    "temperature": 0.7, "confidence_baseline": 0.85,
                    "exploration_rate": 0.3, "conservatism": 0.5,
                    "prompt_mutation_rate": 0.1, "specialization_weight": 1.0,
                    "generation": 0, "parent": "",
                }
                for n in names
            ],
        }
        p = tmp_path / "genomes.json"
        p.write_text(json.dumps(payload))
        return str(p)

    def test_homogeneous_genomes_json_is_healed_and_persisted(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._write_homogeneous_genomes(
                Path(d), ["m%d" % i for i in range(6)])

            # --- first boot (emulates Emperor._load_state) ---
            court = Court()
            court.load_genomes(path)
            court._sm._genome_path = path  # _load_state pins this before heal
            assert genomic_diversity(court._sm._genomes) == 0.0
            healed = court.redisperse_if_homogeneous()
            assert healed is True
            court.save_genomes()  # _load_state persists the cure

            # --- next boot: reload the on-disk file ---
            reloaded = Court()
            reloaded.load_genomes(path)
            # The persisted cure must survive the restart.
            assert genomic_diversity(reloaded._sm._genomes) > 0.15


class TestCrisisGateGenomicDriven:
    def _monitor(self):
        return DiversityMonitor()

    def _homogeneous(self, names):
        return _homogeneous_genomes(names)

    def test_merit_noise_does_not_reset_streak(self):
        """Genomic homogeneity keeps the streak climbing even as merit varies."""
        mon = self._monitor()
        genomes = self._homogeneous(["a", "b", "c", "d"])
        # Merit jumps around every cycle — previously this reset the streak.
        merits = [{"a": 10, "b": 90, "c": 30, "d": 70},
                  {"a": 80, "b": 20, "c": 60, "d": 40},
                  {"a": 50, "b": 50, "c": 50, "d": 50},
                  {"a": 5, "b": 95, "c": 15, "d": 85},
                  {"a": 70, "b": 30, "c": 65, "d": 35},
                  {"a": 45, "b": 55, "c": 48, "d": 52}]
        for m in merits:
            mon.measure(genomes, m, list(genomes.keys()))
        # 6 consecutive genomic-crisis cycles -> streak must reach 6.
        assert mon.get_crisis_streak() == 6
        # With cooldown satisfied, catastrophe becomes available.
        assert mon.is_catastrophe_needed(cycle_count=100) is True

    def test_diverse_genomic_resets_streak(self):
        mon = self._monitor()
        homogeneous = self._homogeneous(["a", "b", "c"])
        for _ in range(3):
            mon.measure(homogeneous, {"a": 50, "b": 50, "c": 50},
                        list(homogeneous.keys()))
        assert mon.get_crisis_streak() == 3
        # Now a genuinely diverse population appears.
        diverse = _archetype_genomes(["d", "e", "f"])
        mon.measure(diverse, {"d": 50, "e": 50, "f": 50},
                    list(diverse.keys()))
        assert mon.get_crisis_streak() == 0

    def test_plan_catastrophe_uses_cycle_count_for_cooldown(self):
        mon = DiversityMonitor()
        mon._crisis_streak = 5
        mon._last_catastrophe_cycle = -mon.CATASTROPHE_COOLDOWN  # cooldown elapsed
        # Sanity: streak satisfied + cooldown elapsed -> catastrophe available.
        assert mon.is_catastrophe_needed(cycle_count=50) is True

        genomes = {n: MinisterGenome(n, "general", temperature=0.7)
                   for n in ["a", "b", "c", "d", "e", "f"]}
        merit = {n: 50 for n in genomes}
        mon.plan_catastrophe(genomes, merit, list(genomes.keys()),
                             list(genomes.keys()), cycle_count=50)
        # Cooldown must now be anchored to the cycle, not active_count.
        assert mon._last_catastrophe_cycle == 50
        # Next cycle is still inside the cooldown window (streak also reset by plan).
        assert mon.is_catastrophe_needed(cycle_count=55) is False
        # Rebuild the streak and confirm the cooldown is genuinely cycle-anchored:
        # 50 + CATASTROPHE_COOLDOWN + 1 must elapse AND the streak must be met again.
        mon._crisis_streak = 5
        assert mon.is_catastrophe_needed(
            cycle_count=50 + mon.CATASTROPHE_COOLDOWN + 1) is True


class TestBredMinisterKeyMapping:
    def test_abstract_traits_map_to_real_genes(self):
        court = Court()
        # Register a parent so _register_bred_minister can resolve generation.
        court.register_many([{"name": "parent", "domain": "general"}])
        bred = {
            "temperature": 0.5,
            "confidence_baseline": 0.8,
            "creativity": 0.9,          # -> exploration_rate
            "thoroughness": 0.1,        # -> conservatism
            "speed": 0.4,               # -> prompt_mutation_rate
            "social_intelligence": 1.7, # -> specialization_weight
        }
        court._sm._register_bred_minister(
            name="bred1", domain="code", genome=bred,
            strategy=BreedingStrategy.EXPLORE, parent="parent",
        )
        g = court._sm._genomes["bred1"]
        assert g.exploration_rate == 0.9
        assert g.conservatism == 0.1
        assert g.prompt_mutation_rate == 0.4
        assert g.specialization_weight == 1.7
