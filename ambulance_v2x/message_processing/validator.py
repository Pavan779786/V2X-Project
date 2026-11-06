"""
Vehicle Data Validation Module
Ensures telemetry sanity, geographic coordinate compliance, speed plausibility,
and freshness before constructing ETSI ITS messages.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List

import config
from data_collection.data_aggregator import VehicleData
from monitoring.logger import get_logger

logger = get_logger("validator")


@dataclass
class ValidationResult:
    """Result of data validation with detailed diagnostics."""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.is_valid:
            return f"Valid (Warnings: {len(self.warnings)})"
        return f"INVALID: {'; '.join(self.errors)}"


class DataValidator:
    """Performs sanity checks on VehicleData before message serialization."""

    def __init__(self, max_age_sec: float = config.DATA_FRESHNESS_MAX_AGE_SEC):
        self.max_age_sec = max_age_sec

    def validate(self, data: VehicleData) -> ValidationResult:
        """
        Validate all vehicle data fields against ETSI ITS physical limits and sanity bounds.
        Returns a ValidationResult object with pass/fail and descriptive error messages.
        """
        errors: List[str] = []
        warnings: List[str] = []

        # 1. Geographic Bounds Check
        if not (config.MIN_LATITUDE <= data.latitude <= config.MAX_LATITUDE):
            errors.append(f"Latitude out of bounds: {data.latitude} (must be in [-90, 90])")
        elif data.latitude == 0.0 and data.longitude == 0.0:
            errors.append("Coordinates at Null Island (0.0, 0.0); GPS fix likely lost")

        if not (config.MIN_LONGITUDE <= data.longitude <= config.MAX_LONGITUDE):
            errors.append(f"Longitude out of bounds: {data.longitude} (must be in [-180, 180])")

        # 2. Speed Bounds Plausibility
        if data.speed_kmh < config.MIN_SPEED_KMH:
            errors.append(f"Negative speed detected: {data.speed_kmh} km/h")
        elif data.speed_kmh > config.MAX_SPEED_KMH:
            errors.append(f"Speed exceeds ambulance operational limit: {data.speed_kmh} km/h (max {config.MAX_SPEED_KMH})")

        # 3. Heading Bounds Check
        if not (config.MIN_HEADING_DEG <= data.heading <= config.MAX_HEADING_DEG):
            errors.append(f"Heading out of bounds: {data.heading} deg (must be in [0, 360])")

        # 4. Timestamp Freshness Check
        now = datetime.now(timezone.utc)
        age_sec = (now - data.timestamp).total_seconds()
        if age_sec > self.max_age_sec:
            errors.append(f"Telemetry stale: age {age_sec:.2f}s exceeds limit {self.max_age_sec:.1f}s")
        elif age_sec < -1.0:
            warnings.append(f"Telemetry timestamp is in future by {-age_sec:.2f}s (clock skew)")

        # 5. GPS Quality Indicators
        if data.gps_fix_quality <= 0:
            warnings.append(f"Poor GPS fix quality: {data.gps_fix_quality}")

        if data.hdop > config.MIN_ACCEPTABLE_HDOP:
            warnings.append(f"High HDOP value: {data.hdop:.1f} (degraded accuracy)")

        if data.satellite_count < config.MIN_ACCEPTABLE_SATELLITES:
            warnings.append(f"Low satellite count: {data.satellite_count}")

        # 6. Acceleration Sanity
        if not (config.MIN_ACCELERATION_MS2 <= data.acceleration <= config.MAX_ACCELERATION_MS2):
            warnings.append(f"Unusual acceleration: {data.acceleration:.2f} m/s^2")

        is_valid = len(errors) == 0

        if not is_valid:
            logger.warning(f"Data validation failed for station {data.station_id}: {'; '.join(errors)}")

        return ValidationResult(is_valid=is_valid, errors=errors, warnings=warnings)
