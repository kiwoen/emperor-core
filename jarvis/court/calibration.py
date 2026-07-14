"""
Confidence Calibrator (置信校准器) — learns each minister's accuracy profile
and adjusts raw confidence toward true reliability.

Core insight: ministers don't know their own accuracy. A minister who claims
95% confidence but is right 60% of the time needs their confidence pulled down.
A minister who claims 60% confidence but is right 90% needs it pushed up.

The Calibrator tracks a per-minister calibration curve using:
1. Bias — systematic offset between predicted confidence and actual outcome
2. Overconfidence Ratio — how much a minister inflates their own estimates
3. Domain Calibration — domain-specific accuracy adjustments
4. Recency Decay — recent outcomes matter more than ancient history

After N decrees, the calibrator produces an adjusted confidence that better
reflects the minister's true reliability.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


class CalibrationMode(Enum):
    """How the calibrator adjusts confidence."""
    RAW = auto()            # Pass through raw confidence unchanged
    BIAS_ONLY = auto()      # Subtract learned bias
    FULL = auto()           # Full calibration (bias + overconfidence + domain)


@dataclass
class CalibrationRecord:
    """A single calibration data point — one minister, one decree."""
    decree_id: str
    minister: str
    domain: str
    raw_confidence: float       # What the minister claimed (0-1)
    actual_outcome: float       # What actually happened (0-1, from feedback)
    timestamp: float            # Unix timestamp
    calibration_error: float = field(init=False)  # abs(raw - actual), computed

    def __post_init__(self) -> None:
        self.calibration_error = abs(self.raw_confidence - self.actual_outcome)


@dataclass
class MinisterCalibration:
    """Aggregated calibration profile for one minister."""
    minister: str

    # Core metrics
    record_count: int = 0
    mean_raw_confidence: float = 0.0      # Average of what they claim
    mean_actual_outcome: float = 0.0      # Average of what actually happens
    bias: float = 0.0                      # Positive = overconfident, negative = underconfident

    # Overconfidence detection
    overconfidence_ratio: float = 1.0      # mean_raw / mean_actual, clamped
    overconfidence_count: int = 0           # How many times they were overconfident

    # Stability
    variance: float = 0.0                  # Variance of calibration errors
    last_updated: float = 0.0

    # Domain-specific
    domain_bias: dict[str, float] = field(default_factory=dict)    # domain -> bias
    domain_counts: dict[str, int] = field(default_factory=dict)    # domain -> sample count

    # Windowed (recent only, for recency-weighted calibration)
    recent_raw: list[float] = field(default_factory=list)
    recent_actual: list[float] = field(default_factory=list)
    max_recent_samples: int = 20


@dataclass
class CalibrationReport:
    """Human-readable calibration report for one minister."""
    minister: str
    record_count: int
    mean_raw: float
    mean_actual: float
    bias: float
    overconfidence_ratio: float
    calibration_mode: CalibrationMode
    domain_breakdown: dict[str, float]      # domain -> bias
    verdict: str                            # One-line summary


class ConfidenceCalibrator:
    """Learns each minister's accuracy profile and adjusts confidence.

    Usage:
        calibrator = ConfidenceCalibrator()

        # Before synthesis
        calibrated = calibrator.calibrate(
            minister_name="chancellor",
            raw_confidence=0.85,
            domain="engineering",
        )

        # After decree outcome is known
        calibrator.update(
            decree_id="decree_42",
            minister_name="chancellor",
            raw_confidence=0.85,
            actual_outcome=0.72,
            domain="engineering",
        )

        # Get a report
        report = calibrator.get_report("chancellor")
    """

    # Calibration thresholds
    MIN_SAMPLES_FOR_BIAS = 3        # Need at least N records before applying bias
    MIN_SAMPLES_FOR_DOMAIN = 5      # Need at least N domain records for domain bias
    BIAS_CAP = 0.30                 # Max bias adjustment in either direction
    OVERCONFIDENCE_CAP = 0.30       # Max overconfidence penalty
    RECENCY_WEIGHT = 0.7            # Weight given to recent vs historical records
    WARMUP_MODE = CalibrationMode.RAW  # Mode when insufficient data

    def __init__(self) -> None:
        self._records: dict[str, list[CalibrationRecord]] = defaultdict(list)
        self._profiles: dict[str, MinisterCalibration] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calibrate(
        self,
        minister_name: str,
        raw_confidence: float,
        domain: str = "general",
        mode: CalibrationMode = CalibrationMode.FULL,
    ) -> float:
        """Calibrate a minister's raw confidence.

        Returns an adjusted confidence in [0, 1] that better reflects
        the minister's historical accuracy.
        """
        profile = self._profiles.get(minister_name)

        if profile is None or profile.record_count < self.MIN_SAMPLES_FOR_BIAS:
            return raw_confidence

        if mode == CalibrationMode.RAW:
            return raw_confidence

        if mode == CalibrationMode.BIAS_ONLY:
            # Simple bias correction
            adjusted = raw_confidence - profile.bias
            return max(0.0, min(1.0, adjusted))

        # ── Full calibration ──
        adjusted = raw_confidence

        # Compute domain-specific overconfidence ratio when sufficient data
        domain_oc_ratio = self._get_domain_overconfidence_ratio(profile, domain)

        # 1. Bias correction (systematic offset)
        bias = self._get_effective_bias(profile, domain)
        if profile.record_count >= self.MIN_SAMPLES_FOR_BIAS:
            adjusted -= bias

        # 2. Overconfidence penalty — domain-aware when data sufficient
        effective_oc = domain_oc_ratio if domain_oc_ratio is not None else profile.overconfidence_ratio
        if effective_oc > 1.0 and profile.overconfidence_count >= 2:
            oc_penalty = min(
                self.OVERCONFIDENCE_CAP,
                (effective_oc - 1.0) * 0.5,
            )
            adjusted -= oc_penalty

        # 3. Variance penalty — unstable ministers get uncertainty penalty
        if profile.variance > 0.15 and profile.record_count >= 5:
            variance_penalty = min(0.15, (profile.variance - 0.15) * 0.3)
            adjusted -= variance_penalty

        # 4. Underconfidence boost — consistently underconfident ministers get lifted
        if effective_oc < 0.85 and profile.mean_raw_confidence < 0.6:
            boost = min(0.15, (0.85 - effective_oc) * 0.3)
            adjusted += boost

        return max(0.0, min(1.0, adjusted))

    def update(
        self,
        decree_id: str,
        minister_name: str,
        raw_confidence: float,
        actual_outcome: float,
        domain: str = "general",
    ) -> None:
        """Record calibration feedback after a decree outcome is known.

        Called automatically after the Emperor issues a decree and
        user feedback (or automated quality metric) is available.
        """
        record = CalibrationRecord(
            decree_id=decree_id,
            minister=minister_name,
            domain=domain,
            raw_confidence=raw_confidence,
            actual_outcome=actual_outcome,
            timestamp=time.time(),
        )

        self._records[minister_name].append(record)
        self._update_profile(minister_name, record)

    def get_report(self, minister_name: str) -> CalibrationReport:
        """Get a human-readable calibration report."""
        profile = self._profiles.get(minister_name)

        if profile is None or profile.record_count == 0:
            return CalibrationReport(
                minister=minister_name,
                record_count=0,
                mean_raw=0.0,
                mean_actual=0.0,
                bias=0.0,
                overconfidence_ratio=1.0,
                calibration_mode=self.WARMUP_MODE,
                domain_breakdown={},
                verdict="数据不足，使用原始置信度",
            )

        # Determine calibration mode
        if profile.record_count < self.MIN_SAMPLES_FOR_BIAS:
            mode = self.WARMUP_MODE
        elif profile.overconfidence_ratio > 1.05 or abs(profile.bias) > 0.05:
            mode = CalibrationMode.FULL
        else:
            mode = CalibrationMode.BIAS_ONLY

        # Verdict
        if profile.overconfidence_ratio > 1.3:
            verdict = f"严重高估（自称 {profile.mean_raw_confidence:.0%}，实际 {profile.mean_actual_outcome:.0%}）"
        elif profile.overconfidence_ratio > 1.1:
            verdict = "略微高估，已施加修正"
        elif profile.overconfidence_ratio < 0.85:
            verdict = "过度谦虚，已提升置信度"
        elif abs(profile.bias) < 0.05:
            verdict = "校准良好，置信度可靠"
        else:
            verdict = f"偏差 {profile.bias:+.0%}，已校准"

        return CalibrationReport(
            minister=minister_name,
            record_count=profile.record_count,
            mean_raw=profile.mean_raw_confidence,
            mean_actual=profile.mean_actual_outcome,
            bias=profile.bias,
            overconfidence_ratio=profile.overconfidence_ratio,
            calibration_mode=mode,
            domain_breakdown=dict(profile.domain_bias),
            verdict=verdict,
        )

    def get_all_reports(self) -> dict[str, CalibrationReport]:
        """Get reports for all tracked ministers."""
        return {
            name: self.get_report(name)
            for name in self._records
        }

    def reset_minister(self, minister_name: str) -> None:
        """Reset calibration data for a minister (e.g., after evolution)."""
        self._records.pop(minister_name, None)
        self._profiles.pop(minister_name, None)

    # ------------------------------------------------------------------
    # Stats & Query
    # ------------------------------------------------------------------

    def get_bias(self, minister_name: str, domain: str = "general") -> float:
        """Get current bias for a minister (0 = perfectly calibrated)."""
        profile = self._profiles.get(minister_name)
        if profile is None:
            return 0.0
        return self._get_effective_bias(profile, domain)

    def get_record_count(self, minister_name: str) -> int:
        """Get number of calibration records for a minister."""
        profile = self._profiles.get(minister_name)
        if profile is None:
            return 0
        return profile.record_count

    def get_calibration_summary(self) -> dict:
        """Get summary stats across all ministers."""
        total_records = sum(len(recs) for recs in self._records.values())
        minister_count = len(self._profiles)
        avg_bias = 0.0
        if minister_count > 0:
            biases = [
                p.bias for p in self._profiles.values()
                if p.record_count >= self.MIN_SAMPLES_FOR_BIAS
            ]
            avg_bias = sum(biases) / max(1, len(biases)) if biases else 0.0

        return {
            "total_records": total_records,
            "tracked_ministers": minister_count,
            "average_bias": avg_bias,
            "calibration_mode": "FULL" if total_records >= self.MIN_SAMPLES_FOR_BIAS * minister_count else "WARMUP",
        }

    # ------------------------------------------------------------------
    # Internal: Profile management
    # ------------------------------------------------------------------

    def _update_profile(self, minister_name: str, record: CalibrationRecord) -> None:
        """Update a minister's calibration profile with a new record."""
        if minister_name not in self._profiles:
            self._profiles[minister_name] = MinisterCalibration(
                minister=minister_name,
            )

        profile = self._profiles[minister_name]
        profile.record_count += 1
        profile.last_updated = time.time()

        # Update sliding windows
        profile.recent_raw.append(record.raw_confidence)
        profile.recent_actual.append(record.actual_outcome)
        if len(profile.recent_raw) > profile.max_recent_samples:
            profile.recent_raw.pop(0)
            profile.recent_actual.pop(0)

        # Recompute means using recency-weighted exponential average
        all_raw = [r.raw_confidence for r in self._records[minister_name]]
        all_actual = [r.actual_outcome for r in self._records[minister_name]]
        all_errors = [r.calibration_error for r in self._records[minister_name]]

        profile.mean_raw_confidence = self._ewma(all_raw)
        profile.mean_actual_outcome = self._ewma(all_actual)

        # Bias = mean(claimed) - mean(actual). Positive → overconfident.
        profile.bias = profile.mean_raw_confidence - profile.mean_actual_outcome

        # Overconfidence ratio
        profile.overconfidence_ratio = (
            profile.mean_raw_confidence / max(0.01, profile.mean_actual_outcome)
        )
        profile.overconfidence_count = sum(
            1 for c in all_raw
            for a in all_actual
            if c > a
        )

        # Variance of calibration errors
        if len(all_errors) >= 2:
            mean_err = sum(all_errors) / len(all_errors)
            profile.variance = sum(
                (e - mean_err) ** 2 for e in all_errors
            ) / len(all_errors)

        # Domain-specific tracking
        domain = record.domain
        if domain not in profile.domain_counts:
            profile.domain_counts[domain] = 0
        profile.domain_counts[domain] += 1

        if domain not in profile.domain_bias:
            profile.domain_bias[domain] = 0.0

        # Recompute domain bias from all records in this domain
        domain_records = [
            r for r in self._records[minister_name]
            if r.domain == domain
        ]
        if len(domain_records) >= self.MIN_SAMPLES_FOR_DOMAIN:
            domain_raw = sum(r.raw_confidence for r in domain_records) / len(domain_records)
            domain_actual = sum(r.actual_outcome for r in domain_records) / len(domain_records)
            profile.domain_bias[domain] = domain_raw - domain_actual

    def _get_effective_bias(self, profile: MinisterCalibration, domain: str) -> float:
        """Get the effective bias, preferring domain-specific if available."""
        domain_bias = profile.domain_bias.get(domain, 0.0)
        domain_count = profile.domain_counts.get(domain, 0)

        if domain_count >= self.MIN_SAMPLES_FOR_DOMAIN:
            # Blend global and domain bias, weighted by domain sample count
            domain_weight = min(1.0, domain_count / 20.0)
            bias = domain_bias * domain_weight + profile.bias * (1.0 - domain_weight)
        else:
            bias = profile.bias

        return max(-self.BIAS_CAP, min(self.BIAS_CAP, bias))

    def _get_domain_overconfidence_ratio(
        self, profile: MinisterCalibration, domain: str
    ) -> Optional[float]:
        """Compute domain-specific overconfidence ratio if data is sufficient.

        Returns None if insufficient domain data; caller falls back to global.
        """
        domain_count = profile.domain_counts.get(domain, 0)
        if domain_count < self.MIN_SAMPLES_FOR_DOMAIN:
            return None

        domain_records = [
            r for r in self._records.get(profile.minister, [])
            if r.domain == domain
        ]
        if not domain_records:
            return None

        domain_raw = sum(r.raw_confidence for r in domain_records) / len(domain_records)
        domain_actual = sum(r.actual_outcome for r in domain_records) / len(domain_records)
        if domain_actual < 0.01:
            return profile.overconfidence_ratio  # Avoid division by near-zero
        return domain_raw / domain_actual

    @staticmethod
    def _ewma(values: list[float], alpha: float = 0.15) -> float:
        """Exponentially weighted moving average (recent-biased)."""
        if not values:
            return 0.0
        result = values[0]
        for v in values[1:]:
            result = alpha * v + (1.0 - alpha) * result
        return result
