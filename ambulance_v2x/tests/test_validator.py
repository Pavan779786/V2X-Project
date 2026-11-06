"""
Unit Tests for Vehicle Data Validator
Verifies range boundaries, physical plausibility, and freshness rules.
"""

from datetime import datetime, timezone, timedelta
import pytest

from data_collection.data_aggregator import VehicleData
from message_processing.validator import DataValidator


@pytest.fixture
def valid_vehicle_data():
    """Fixture providing a standard valid VehicleData instance."""
    return VehicleData(
        station_id=1042,
        timestamp=datetime.now(timezone.utc),
        latitude=52.520008,
        longitude=13.404954,
        altitude=45.0,
        speed_kmh=65.0,
        heading=120.5,
        acceleration=1.2,
        vehicle_width=210,
        vehicle_length=620,
        emergency_active=True,
        siren_active=True,
        lights_active=True,
        gps_fix_quality=2,
        satellite_count=10,
        hdop=1.1,
        can_connected=True,
        obu_link_quality=0.95,
    )


def test_validator_valid_data(valid_vehicle_data):
    validator = DataValidator()
    result = validator.validate(valid_vehicle_data)
    assert result.is_valid is True
    assert len(result.errors) == 0


def test_validator_latitude_bounds(valid_vehicle_data):
    validator = DataValidator()

    # Upper bound breach
    valid_vehicle_data.latitude = 95.0
    result = validator.validate(valid_vehicle_data)
    assert result.is_valid is False
    assert any("Latitude out of bounds" in err for err in result.errors)

    # Lower bound breach
    valid_vehicle_data.latitude = -91.0
    result = validator.validate(valid_vehicle_data)
    assert result.is_valid is False

    # Edge cases
    valid_vehicle_data.latitude = 90.0
    assert validator.validate(valid_vehicle_data).is_valid is True
    valid_vehicle_data.latitude = -90.0
    assert validator.validate(valid_vehicle_data).is_valid is True


def test_validator_longitude_bounds(valid_vehicle_data):
    validator = DataValidator()

    valid_vehicle_data.longitude = 185.0
    result = validator.validate(valid_vehicle_data)
    assert result.is_valid is False
    assert any("Longitude out of bounds" in err for err in result.errors)

    valid_vehicle_data.longitude = -181.0
    assert validator.validate(valid_vehicle_data).is_valid is False

    valid_vehicle_data.longitude = 180.0
    assert validator.validate(valid_vehicle_data).is_valid is True


def test_validator_null_island_rejection(valid_vehicle_data):
    validator = DataValidator()
    valid_vehicle_data.latitude = 0.0
    valid_vehicle_data.longitude = 0.0
    result = validator.validate(valid_vehicle_data)
    assert result.is_valid is False
    assert any("Null Island" in err for err in result.errors)


def test_validator_speed_plausibility(valid_vehicle_data):
    validator = DataValidator()

    # Negative speed
    valid_vehicle_data.speed_kmh = -5.0
    result = validator.validate(valid_vehicle_data)
    assert result.is_valid is False
    assert any("Negative speed" in err for err in result.errors)

    # Excessive speed
    valid_vehicle_data.speed_kmh = 240.0
    result = validator.validate(valid_vehicle_data)
    assert result.is_valid is False
    assert any("exceeds ambulance operational limit" in err for err in result.errors)

    # Valid boundary speed
    valid_vehicle_data.speed_kmh = 0.0
    assert validator.validate(valid_vehicle_data).is_valid is True
    valid_vehicle_data.speed_kmh = 199.9
    assert validator.validate(valid_vehicle_data).is_valid is True


def test_validator_heading_bounds(valid_vehicle_data):
    validator = DataValidator()

    valid_vehicle_data.heading = -0.5
    assert validator.validate(valid_vehicle_data).is_valid is False

    valid_vehicle_data.heading = 360.5
    assert validator.validate(valid_vehicle_data).is_valid is False

    valid_vehicle_data.heading = 0.0
    assert validator.validate(valid_vehicle_data).is_valid is True

    valid_vehicle_data.heading = 360.0
    assert validator.validate(valid_vehicle_data).is_valid is True


def test_validator_timestamp_freshness(valid_vehicle_data):
    validator = DataValidator(max_age_sec=2.0)

    # 3 seconds old data -> rejected
    valid_vehicle_data.timestamp = datetime.now(timezone.utc) - timedelta(seconds=3.0)
    result = validator.validate(valid_vehicle_data)
    assert result.is_valid is False
    assert any("stale" in err for err in result.errors)

    # 1 second old data -> accepted
    valid_vehicle_data.timestamp = datetime.now(timezone.utc) - timedelta(seconds=1.0)
    result = validator.validate(valid_vehicle_data)
    assert result.is_valid is True


def test_validator_warnings_on_poor_gps(valid_vehicle_data):
    validator = DataValidator()

    valid_vehicle_data.gps_fix_quality = 0
    valid_vehicle_data.hdop = 8.5
    valid_vehicle_data.satellite_count = 2

    result = validator.validate(valid_vehicle_data)
    # Warnings do not invalidate payload unless hard errors exist
    assert result.is_valid is True
    assert len(result.warnings) >= 3
