"""
Unit Tests for ETSI EN 302 637-2 CAM Builder
Validates protocol dictionary construction, unit conversions, and container completeness.
"""

from datetime import datetime, timezone
import pytest

from data_collection.data_aggregator import VehicleData
from message_processing.cam_builder import (
    CAMBuilder,
    convert_lat_lon_to_etsi,
    convert_speed_to_etsi,
    convert_heading_to_etsi,
    convert_acceleration_to_etsi,
)
from message_processing.message_serializer import MessageSerializer


@pytest.fixture
def sample_vehicle_data():
    return VehicleData(
        station_id=12345,
        timestamp=datetime.now(timezone.utc),
        latitude=52.520008,
        longitude=13.404954,
        altitude=45.0,
        speed_kmh=36.0,  # Exactly 10 m/s -> 1000 ETSI units
        heading=180.5,   # 1805 ETSI units
        acceleration=2.0,# 20 ETSI units
        vehicle_width=210,
        vehicle_length=620,
        emergency_active=True,
        siren_active=True,
        lights_active=True,
        gps_fix_quality=2,
        satellite_count=12,
        hdop=0.9,
    )


def test_unit_conversions():
    # Lat/Lon scale: 1e7
    assert convert_lat_lon_to_etsi(52.520008, is_lat=True) == 525200080
    assert convert_lat_lon_to_etsi(13.404954, is_lat=False) == 134049540

    # Speed: 36 km/h = 10.0 m/s -> 1000
    assert convert_speed_to_etsi(36.0) == 1000
    # 72 km/h = 20.0 m/s -> 2000
    assert convert_speed_to_etsi(72.0) == 2000
    # 0 km/h -> 0
    assert convert_speed_to_etsi(0.0) == 0

    # Heading: 0.1 degree
    assert convert_heading_to_etsi(90.0) == 900
    assert convert_heading_to_etsi(180.5) == 1805

    # Acceleration: 0.1 m/s^2
    assert convert_acceleration_to_etsi(2.5) == 25
    assert convert_acceleration_to_etsi(-1.8) == -18


def test_cam_header_structure(sample_vehicle_data):
    builder = CAMBuilder(station_id=12345)
    cam = builder.build(sample_vehicle_data)

    assert "header" in cam
    header = cam["header"]
    assert header["protocolVersion"] == 2
    assert header["messageID"] == 2
    assert header["stationID"] == 12345


def test_cam_basic_container(sample_vehicle_data):
    builder = CAMBuilder(station_id=12345)
    cam = builder.build(sample_vehicle_data)

    basic = cam["cam"]["camParameters"]["basicContainer"]
    assert basic["stationType"] == 10  # Special Vehicle
    pos = basic["referencePosition"]
    assert pos["latitude"] == 525200080
    assert pos["longitude"] == 134049540
    assert pos["altitude"] == 4500  # 45m * 100


def test_cam_high_frequency_container(sample_vehicle_data):
    builder = CAMBuilder(station_id=12345)
    cam = builder.build(sample_vehicle_data)

    hf = cam["cam"]["camParameters"]["highFrequencyContainer"]["basicVehicleContainerHighFrequency"]
    assert hf["speed"]["speedValue"] == 1000
    assert hf["heading"]["headingValue"] == 1805
    assert hf["vehicleLength"]["vehicleLengthValue"] == 62
    assert hf["vehicleWidth"] == 21
    assert hf["longitudinalAcceleration"]["longitudinalAccelerationValue"] == 20
    assert hf["driveDirection"] == "forward"


def test_cam_special_vehicle_container_emergency_active(sample_vehicle_data):
    builder = CAMBuilder(station_id=12345)
    cam = builder.build(sample_vehicle_data)

    special = cam["cam"]["camParameters"]["specialVehicleContainer"]["emergencyContainer"]
    assert special["lightBarSirenInUse"]["sirenActive"] is True
    assert special["lightBarSirenInUse"]["beaconLight"] is True
    assert special["causeCode"]["causeCode"] == 9
    assert special["causeCode"]["subCauseCode"] == 2  # Mobile (speed > 1 km/h)
    assert "requestForRightOfWay" in special["emergencyPriority"]
    assert "requestForFreeCrossingAtTrafficLight" in special["emergencyPriority"]


def test_cam_special_vehicle_container_stationary(sample_vehicle_data):
    builder = CAMBuilder(station_id=12345)
    sample_vehicle_data.speed_kmh = 0.0  # Stopped
    cam = builder.build(sample_vehicle_data)

    special = cam["cam"]["camParameters"]["specialVehicleContainer"]["emergencyContainer"]
    assert special["causeCode"]["subCauseCode"] == 1  # Stationary


def test_cam_serialization_cycle(sample_vehicle_data):
    builder = CAMBuilder(station_id=12345)
    cam = builder.build(sample_vehicle_data)

    # Test JSON round-trip
    json_bytes = MessageSerializer.to_json_bytes(cam)
    assert isinstance(json_bytes, bytes)
    deserialized = MessageSerializer.from_json_bytes(json_bytes)
    assert deserialized["header"]["stationID"] == 12345
    assert deserialized["cam"]["camParameters"]["basicContainer"]["stationType"] == 10
