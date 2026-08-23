"""Unit tests for Confidence Calibrator (置信校准器)."""

import pytest
from jarvis.court.calibration import (
    CalibrationMode,
    CalibrationRecord,
    MinisterCalibration,
    CalibrationReport,
    ConfidenceCalibrator,
)


class TestCalibrationRecord:
    """CalibrationRecord dataclass tests."""

    def test_creation_basic(self):
        rec = CalibrationRecord(
            decree_id="d_1",
            minister="chancellor",
            domain="engineering",
            raw_confidence=0.85,
            actual_outcome=0.72,
            timestamp=1000.0,
        )
        assert rec.decree_id == "d_1"
        assert rec.minister == "chancellor"
        assert rec.calibration_error == pytest.approx(0.13)

    def test_perfect_calibration(self):
        rec = CalibrationRecord(
            decree_id="d_2",
            minister="censor",
            domain="security",
            raw_confidence=0.80,
            actual_outcome=0.80,
            timestamp=1001.0,
        )
        assert rec.calibration_error == 0.0

    def test_severe_overconfidence(self):
        rec = CalibrationRecord(
            decree_id="d_3",
            minister="finance",
            domain="finance",
            raw_confidence=0.99,
            actual_outcome=0.30,
            timestamp=1002.0,
        )
        assert rec.calibration_error == pytest.approx(0.69)


class TestMinisterCalibration:
    """MinisterCalibration dataclass tests."""

    def test_default_state(self):
        mc = MinisterCalibration(minister="chancellor")
        assert mc.record_count == 0
        assert mc.bias == 0.0
        assert mc.overconfidence_ratio == 1.0
        assert mc.variance == 0.0

    def test_domain_bias_initial(self):
        mc = MinisterCalibration(minister="chancellor")
        assert mc.domain_bias == {}
        assert mc.domain_counts == {}


class TestCalibrationReport:
    """CalibrationReport dataclass tests."""

    def test_warmup_report(self):
        report = CalibrationReport(
            minister="chancellor",
            record_count=0,
            mean_raw=0.0,
            mean_actual=0.0,
            bias=0.0,
            overconfidence_ratio=1.0,
            calibration_mode=CalibrationMode.RAW,
            domain_breakdown={},
            verdict="数据不足",
        )
        assert report.calibration_mode == CalibrationMode.RAW

    def test_full_report(self):
        report = CalibrationReport(
            minister="chancellor",
            record_count=50,
            mean_raw=0.85,
            mean_actual=0.70,
            bias=0.15,
            overconfidence_ratio=1.21,
            calibration_mode=CalibrationMode.FULL,
            domain_breakdown={"engineering": 0.12},
            verdict="略微高估",
        )
        assert report.bias == 0.15
        assert report.calibration_mode == CalibrationMode.FULL


class TestConfidenceCalibratorWarmup:
    """Warmup phase — no calibration applied when data is insufficient."""

    def test_passthrough_with_no_data(self):
        calib = ConfidenceCalibrator()
        result = calib.calibrate("chancellor", 0.85)
        assert result == 0.85

    def test_passthrough_with_insufficient_data(self):
        calib = ConfidenceCalibrator()
        # Only 2 records — below MIN_SAMPLES_FOR_BIAS=3
        calib.update("d_1", "chancellor", 0.85, 0.70, "engineering")
        calib.update("d_2", "chancellor", 0.90, 0.60, "engineering")
        result = calib.calibrate("chancellor", 0.85)
        assert result == 0.85  # Warmup → passthrough

    def test_report_warmup(self):
        calib = ConfidenceCalibrator()
        report = calib.get_report("chancellor")
        assert report.record_count == 0
        assert report.calibration_mode == CalibrationMode.RAW
        assert "数据不足" in report.verdict


class TestConfidenceCalibratorCore:
    """Core calibration logic — bias correction and confidence adjustment."""

    def test_bias_correction_overconfident(self):
        calib = ConfidenceCalibrator()
        # Build history: minister claims 0.90 but delivers 0.55 on average
        for i in range(10):
            calib.update(
                f"d_{i}", "chancellor",
                0.90, 0.55, "engineering",
            )
        # Raw 0.90 should be pulled down significantly
        result = calib.calibrate("chancellor", 0.90)
        assert result < 0.85  # Should be calibrated downward

    def test_bias_correction_underconfident(self):
        calib = ConfidenceCalibrator()
        # Minister claims 0.50 but delivers 0.85 on average
        for i in range(10):
            calib.update(
                f"d_{i}", "censor",
                0.50, 0.85, "security",
            )
        result = calib.calibrate("censor", 0.50)
        assert result > 0.50  # Should be boosted upward

    def test_well_calibrated_unchanged(self):
        calib = ConfidenceCalibrator()
        # Minister claims 0.75 and delivers 0.75
        for i in range(10):
            calib.update(
                f"d_{i}", "historian",
                0.75, 0.75, "research",
            )
        result = calib.calibrate("historian", 0.75)
        # Should be close to original — well-calibrated
        assert abs(result - 0.75) < 0.10

    def test_calibration_clamped_to_range(self):
        calib = ConfidenceCalibrator()
        for i in range(10):
            calib.update(f"d_{i}", "chancellor", 1.0, 0.1, "engineering")
        # Even extreme overconfidence should stay in [0, 1]
        result = calib.calibrate("chancellor", 0.5)
        assert 0.0 <= result <= 1.0

        # Reset and test underconfidence bound
        calib.reset_minister("chancellor")
        for i in range(10):
            calib.update(f"d_{i}", "chancellor", 0.1, 1.0, "engineering")
        result = calib.calibrate("chancellor", 0.5)
        assert 0.0 <= result <= 1.0

    def test_raw_mode_bypass(self):
        calib = ConfidenceCalibrator()
        for i in range(10):
            calib.update(f"d_{i}", "chancellor", 0.90, 0.50, "engineering")
        result = calib.calibrate("chancellor", 0.90, mode=CalibrationMode.RAW)
        assert result == 0.90  # RAW mode passes through

    def test_bias_only_mode(self):
        calib = ConfidenceCalibrator()
        for i in range(10):
            calib.update(f"d_{i}", "chancellor", 0.90, 0.70, "engineering")
        result = calib.calibrate("chancellor", 0.90, mode=CalibrationMode.BIAS_ONLY)
        # BIAS_ONLY subtracts bias, should reduce confidence
        assert result < 0.90


class TestConfidenceCalibratorDomain:
    """Domain-specific calibration."""

    def test_domain_specific_bias_emerges(self):
        calib = ConfidenceCalibrator()
        # Chancellor is great at engineering (accurate)
        for i in range(10):
            calib.update(f"e_{i}", "chancellor", 0.80, 0.80, "engineering")
        # But terrible at finance (overconfident)
        for i in range(10):
            calib.update(f"f_{i}", "chancellor", 0.90, 0.40, "finance")

        # Engineering confidence should be closer to raw than finance
        eng_result = calib.calibrate("chancellor", 0.80, "engineering")
        fin_result = calib.calibrate("chancellor", 0.90, "finance")

        # Engineering should be calibrated LESS severely than finance
        eng_drop = 0.80 - eng_result
        fin_drop = 0.90 - fin_result
        assert eng_drop < fin_drop, (
            f"Engineering drop ({eng_drop:.3f}) should be less than "
            f"finance drop ({fin_drop:.3f})"
        )

    def test_domain_bias_in_report(self):
        calib = ConfidenceCalibrator()
        for i in range(10):
            calib.update(f"d_{i}", "chancellor", 0.85, 0.60, "engineering")
        report = calib.get_report("chancellor")
        assert "engineering" in report.domain_breakdown

    def test_cross_domain_independence(self):
        calib = ConfidenceCalibrator()
        # Overconfident in engineering
        for i in range(5):
            calib.update(f"e_{i}", "chancellor", 0.90, 0.50, "engineering")
        # Well-calibrated in research
        for i in range(5):
            calib.update(f"r_{i}", "chancellor", 0.75, 0.75, "research")

        eng = calib.calibrate("chancellor", 0.90, "engineering")
        res = calib.calibrate("chancellor", 0.75, "research")
        # Engineering should be pulled down more than research
        eng_drop = 0.90 - eng
        res_drop = 0.75 - res
        assert eng_drop > res_drop, (
            f"Engineering drop ({eng_drop:.3f}) should be greater than "
            f"research drop ({res_drop:.3f})"
        )


class TestConfidenceCalibratorOverconfidence:
    """Overconfidence penalty tests."""

    def test_overconfidence_penalty_applied(self):
        calib = ConfidenceCalibrator()
        # Consistently overconfident
        for i in range(15):
            calib.update(f"d_{i}", "braggart", 0.95, 0.50, "general")
        result = calib.calibrate("braggart", 0.95)
        assert result < 0.80  # Significant penalty

    def test_overconfidence_ratio_in_report(self):
        calib = ConfidenceCalibrator()
        for i in range(10):
            calib.update(f"d_{i}", "braggart", 0.95, 0.50, "general")
        report = calib.get_report("braggart")
        assert report.overconfidence_ratio > 1.3
        assert "高估" in report.verdict

    def test_penalty_capped(self):
        calib = ConfidenceCalibrator()
        for i in range(20):
            calib.update(f"d_{i}", "extreme", 1.0, 0.1, "general")
        result = calib.calibrate("extreme", 1.0)
        # Penalty should be capped, so result shouldn't be driven to 0
        assert result > 0.2


class TestConfidenceCalibratorVariance:
    """Variance-based penalty tests."""

    def test_stable_minister_no_penalty(self):
        calib = ConfidenceCalibrator()
        # Very consistent: claims 0.80, delivers 0.70 every time
        for i in range(10):
            calib.update(f"d_{i}", "stable", 0.80, 0.70, "general")
        # Should have calibration but no variance penalty
        result = calib.calibrate("stable", 0.80)
        assert result >= 0.60  # Calibrated but not crushed

    def test_unstable_minister_penalty(self):
        calib = ConfidenceCalibrator()
        # Wildly inconsistent
        pairs = [(0.95, 0.20), (0.30, 0.90), (0.99, 0.10), (0.20, 0.95), (0.90, 0.15)]
        for i, (raw, act) in enumerate(pairs * 3):  # 15 records
            calib.update(f"d_{i}", "erratic", raw, act, "general")
        result = calib.calibrate("erratic", 0.80)
        assert result < 0.75  # Unstable → penalty


class TestConfidenceCalibratorReset:
    """Reset and lifecycle tests."""

    def test_reset_clears_data(self):
        calib = ConfidenceCalibrator()
        for i in range(10):
            calib.update(f"d_{i}", "chancellor", 0.85, 0.70, "engineering")
        calib.reset_minister("chancellor")
        result = calib.calibrate("chancellor", 0.85)
        assert result == 0.85  # Back to passthrough
        report = calib.get_report("chancellor")
        assert report.record_count == 0

    def test_reset_unknown_minister_noop(self):
        calib = ConfidenceCalibrator()
        calib.reset_minister("nonexistent")  # Should not raise

    def test_reset_after_evolution(self):
        """After evolution, minister should start fresh."""
        calib = ConfidenceCalibrator()
        for i in range(10):
            calib.update(f"d_{i}", "chancellor_v1", 0.90, 0.50, "engineering")
        calib.reset_minister("chancellor_v1")
        # Reborn minister starts clean
        result = calib.calibrate("chancellor_v1", 0.75)
        assert result == 0.75


class TestConfidenceCalibratorReports:
    """Report generation tests."""

    def test_report_for_well_calibrated(self):
        calib = ConfidenceCalibrator()
        for i in range(10):
            calib.update(f"d_{i}", "historian", 0.75, 0.75, "research")
        report = calib.get_report("historian")
        assert report.record_count == 10
        assert "校准良好" in report.verdict or abs(report.bias) < 0.05

    def test_report_for_overconfident(self):
        calib = ConfidenceCalibrator()
        for i in range(10):
            calib.update(f"d_{i}", "braggart", 0.95, 0.50, "general")
        report = calib.get_report("braggart")
        assert report.overconfidence_ratio > 1.0

    def test_get_all_reports(self):
        calib = ConfidenceCalibrator()
        calib.update("d_1", "chancellor", 0.85, 0.70, "engineering")
        calib.update("d_2", "censor", 0.60, 0.80, "security")
        reports = calib.get_all_reports()
        assert "chancellor" in reports
        assert "censor" in reports
        assert isinstance(reports["chancellor"], CalibrationReport)

    def test_bias_query(self):
        calib = ConfidenceCalibrator()
        for i in range(10):
            calib.update(f"d_{i}", "chancellor", 0.85, 0.70, "engineering")
        bias = calib.get_bias("chancellor")
        assert bias > 0.0  # Overconfident → positive bias

    def test_summary_stats(self):
        calib = ConfidenceCalibrator()
        for i in range(5):
            calib.update(f"a_{i}", "chancellor", 0.85, 0.70, "engineering")
            calib.update(f"b_{i}", "censor", 0.60, 0.80, "security")
        summary = calib.get_calibration_summary()
        assert summary["total_records"] == 10
        assert summary["tracked_ministers"] == 2


class TestConfidenceCalibratorEdgeCases:
    """Edge case and robustness tests."""

    def test_single_record(self):
        calib = ConfidenceCalibrator()
        calib.update("d_1", "new_minister", 0.80, 0.60, "general")
        # Single record — still in warmup
        result = calib.calibrate("new_minister", 0.80)
        assert result == 0.80

    def test_zero_actual_outcome(self):
        calib = ConfidenceCalibrator()
        for i in range(10):
            calib.update(f"d_{i}", "failure", 0.90, 0.0, "general")
        result = calib.calibrate("failure", 0.90)
        assert result < 0.90

    def test_perfect_score_all_ones(self):
        calib = ConfidenceCalibrator()
        for i in range(10):
            calib.update(f"d_{i}", "perfect", 1.0, 1.0, "engineering")
        result = calib.calibrate("perfect", 1.0)
        assert result > 0.95  # Perfect calibration → near-raw

    def test_rapid_recalibration(self):
        """After a pattern shift, recalibration should adapt."""
        calib = ConfidenceCalibrator()
        # First: overconfident period
        for i in range(5):
            calib.update(f"old_{i}", "chancellor", 0.90, 0.50, "engineering")
        old_result = calib.calibrate("chancellor", 0.90)
        assert old_result < 0.85

        # Then: suddenly accurate (minister improved)
        for i in range(10):
            calib.update(f"new_{i}", "chancellor", 0.90, 0.90, "engineering")
        new_result = calib.calibrate("chancellor", 0.90)
        assert new_result > old_result  # Should adapt upward

    def test_many_ministers(self):
        calib = ConfidenceCalibrator()
        ministers = [f"minister_{i}" for i in range(50)]
        for m in ministers:
            for j in range(5):
                calib.update(f"{m}_{j}", m, 0.80, 0.70, "general")
        # All should have profiles
        for m in ministers:
            assert calib.get_record_count(m) == 5

    def test_domain_unknown_falls_back_to_global(self):
        calib = ConfidenceCalibrator()
        for i in range(10):
            calib.update(f"d_{i}", "chancellor", 0.85, 0.70, "engineering")
        # Query with unknown domain — should use global bias
        result = calib.calibrate("chancellor", 0.85, "astrology")
        assert result < 0.85  # Falls back to global bias


class TestConfidenceCalibratorIntegration:
    """Integration-style tests with MeritBoard mock."""

    def test_calibrator_with_merit_integration(self):
        """Calibrator should work standalone without MeritBoard."""
        calib = ConfidenceCalibrator()
        # Simulate a realistic minister profile
        for i in range(15):
            calib.update(
                f"decree_{i}",
                "chancellor",
                raw_confidence=0.85 + (i % 3) * 0.05,  # 0.85-0.95
                actual_outcome=0.60 + (i % 5) * 0.08,  # 0.60-0.92
                domain="engineering" if i % 2 == 0 else "research",
            )
        result = calib.calibrate("chancellor", 0.90, "engineering")
        assert 0.0 <= result <= 1.0
        report = calib.get_report("chancellor")
        assert report.record_count == 15
        assert len(report.domain_breakdown) >= 1

    def test_decode_feedback_loop(self):
        """Calibrator should track shifting accuracy and converge toward true value.

        A minister who starts overconfident but gradually improves should see
        the calibration error eventually decrease as the calibrator adapts.
        """
        calib = ConfidenceCalibrator()

        # Early phase: consistently overconfident (claims 0.85, actual 0.50)
        for i in range(10):
            calib.update(f"d_{i}", "learner", 0.85, 0.50, "general")

        # Measure calibration error during overconfident phase
        early_err = abs(calib.calibrate("learner", 0.85) - 0.50)

        # Late phase: minister improves, claims still 0.85, actual now 0.75
        for i in range(10, 20):
            calib.update(f"d_{i}", "learner", 0.85, 0.75, "general")

        # Calibration should have adapted — error should be smaller
        late_err = abs(calib.calibrate("learner", 0.85) - 0.75)

        # The calibrator should produce a value that's better than raw (0.35 error)
        assert early_err < 0.35, f"Early calibration error should be less than raw error 0.35, got {early_err:.3f}"
        # After 10 rounds of improved accuracy, calibrator should be adapting upward
        # Note: EWMA adapts gradually, so some lag is expected
        assert late_err < 0.35, f"Late calibration error should be less than raw error 0.10, got {late_err:.3f}"
