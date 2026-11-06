"""
ETSI EN 302 637-2 CAM (Cooperative Awareness Message) Builder
Constructs standardized CAM messages containing Basic, High Frequency,
and Special Emergency Vehicle containers with proper ITS unit scalings.
"""

from datetime import datetime, timezone
from typing import Dict, Any, List

import config
from data_collection.data_aggregator import VehicleData
from monitoring.logger import get_logger

logger = get_logger("cam_builder")

# ETSI ITS Epoch: 2004-01-01 00:00:00 UTC
ITS_EPOCH = datetime(2004, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def get_generation_delta_time(dt: datetime) -> int:
    """Calculate generationDeltaTime in milliseconds mod 65536 per ETSI ITS."""
    delta = dt - ITS_EPOCH
    ms = int(delta.total_seconds() * 1000)
    return ms % 65536


def convert_lat_lon_to_etsi(val: float, is_lat: bool = True) -> int:
    """Scale latitude/longitude into 1/10 micro-degrees (1e-7 deg)."""
    if is_lat and not (-90.0 <= val <= 90.0):
        return config.ETSI_LATITUDE_UNAVAILABLE
    if not is_lat and not (-180.0 <= val <= 180.0):
        return config.ETSI_LONGITUDE_UNAVAILABLE
    return int(round(val * config.LAT_LON_SCALE_FACTOR))


def convert_speed_to_etsi(speed_kmh: float) -> int:
    """Convert speed from km/h to ETSI 0.01 m/s units (0..16383)."""
    if speed_kmh < 0.0:
        return 0
    speed_mps = speed_kmh * config.KMH_TO_MPS
    val = int(round(speed_mps * config.SPEED_SCALE_FACTOR_MPS_TO_ETSI))
    return min(val, 16382)


def convert_heading_to_etsi(heading_deg: float) -> int:
    """Convert heading (0..360) to ETSI 0.1 degree units (0..3600)."""
    if not (0.0 <= heading_deg <= 360.0):
        return config.ETSI_HEADING_UNAVAILABLE
    val = int(round(heading_deg * config.HEADING_SCALE_FACTOR)) % 3600
    return 3600 if val == 0 and heading_deg > 0 else val


def convert_acceleration_to_etsi(accel_ms2: float) -> int:
    """Convert acceleration (m/s^2) to ETSI 0.1 m/s^2 units (-160..160)."""
    val = int(round(accel_ms2 * config.ACCEL_SCALE_FACTOR))
    return max(-160, min(160, val))


class CAMBuilder:
    """ETSI EN 302 637-2 CAM Message Builder for Emergency Ambulances."""

    def __init__(self, station_id: int = config.STATION_ID):
        self.station_id = station_id

    def build(self, data: VehicleData) -> Dict[str, Any]:
        """
        Build an ETSI-compliant CAM dictionary from VehicleData.
        Converts real physical units to standard ETSI ITS integers.
        """
        gen_delta_time = get_generation_delta_time(data.timestamp)

        # ETSI units conversions
        lat_etsi = convert_lat_lon_to_etsi(data.latitude, is_lat=True)
        lon_etsi = convert_lat_lon_to_etsi(data.longitude, is_lat=False)
        alt_etsi = int(round(data.altitude * config.ALTITUDE_SCALE_FACTOR))
        speed_etsi = convert_speed_to_etsi(data.speed_kmh)
        heading_etsi = convert_heading_to_etsi(data.heading)
        accel_etsi = convert_acceleration_to_etsi(data.acceleration)

        # Length and width in 0.1 meter units (e.g. 620cm = 62; 210cm = 21)
        length_val = max(1, int(round(data.vehicle_length / 10.0)))
        width_val = max(1, int(round(data.vehicle_width / 10.0)))

        # Cause Code selection: 1 if stationary, 2 if approaching mobile
        sub_cause = (
            config.SUB_CAUSE_CODE_STATIONARY
            if data.speed_kmh < 1.0
            else config.SUB_CAUSE_CODE_APPROACHING_MOBILE
        )

        emergency_priorities: List[str] = []
        if data.emergency_active:
            emergency_priorities = [
                config.EMERGENCY_PRIORITY_RIGHT_OF_WAY,
                config.EMERGENCY_PRIORITY_TRAFFIC_LIGHT,
            ]

        cam_message: Dict[str, Any] = {
            "header": {
                "protocolVersion": config.ETSI_PROTOCOL_VERSION,
                "messageID": config.MESSAGE_ID_CAM,
                "stationID": data.station_id,
            },
            "cam": {
                "generationDeltaTime": gen_delta_time,
                "camParameters": {
                    "basicContainer": {
                        "stationType": config.STATION_TYPE_SPECIAL_VEHICLE,
                        "referencePosition": {
                            "latitude": lat_etsi,
                            "longitude": lon_etsi,
                            "altitude": alt_etsi,
                        },
                    },
                    "highFrequencyContainer": {
                        "basicVehicleContainerHighFrequency": {
                            "heading": {
                                "headingValue": heading_etsi,
                                "headingConfidence": 1,
                            },
                            "speed": {
                                "speedValue": speed_etsi,
                                "speedConfidence": 1,
                            },
                            "driveDirection": "forward",
                            "vehicleLength": {
                                "vehicleLengthValue": length_val,
                            },
                            "vehicleWidth": width_val,
                            "longitudinalAcceleration": {
                                "longitudinalAccelerationValue": accel_etsi,
                            },
                            "curvature": {
                                "curvatureValue": 0,
                                "curvatureCalculationMode": "unavailable",
                            },
                            "yawRate": {
                                "yawRateValue": 0,
                            },
                        }
                    },
                    "specialVehicleContainer": {
                        "emergencyContainer": {
                            "lightBarSirenInUse": {
                                "sirenActive": data.siren_active,
                                "beaconLight": data.lights_active,
                            },
                            "causeCode": {
                                "causeCode": config.CAUSE_CODE_EMERGENCY_VEHICLE,
                                "subCauseCode": sub_cause,
                            },
                            "emergencyPriority": emergency_priorities,
                        }
                    },
                },
            },
        }

        return cam_message
