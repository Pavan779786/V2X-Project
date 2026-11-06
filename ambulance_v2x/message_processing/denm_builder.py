"""
ETSI EN 302 637-3 DENM (Decentralized Environmental Notification Message) Builder
Generates safety alerts for emergency vehicle approach (CauseCode 9),
specifying actionID, relevance area, validity duration, and event position.
"""

from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

import config
from data_collection.data_aggregator import VehicleData
from monitoring.logger import get_logger
from .cam_builder import (
    convert_lat_lon_to_etsi,
    convert_speed_to_etsi,
    convert_heading_to_etsi,
    ITS_EPOCH,
)

logger = get_logger("denm_builder")


def get_its_timestamp_ms(dt: datetime) -> int:
    """Return milliseconds elapsed since ETSI ITS Epoch (2004-01-01T00:00:00Z)."""
    delta = dt - ITS_EPOCH
    return int(delta.total_seconds() * 1000)


class DENMBuilder:
    """
    Builder for ETSI EN 302 637-3 DENM messages alerting approaching vehicles
    and infrastructure to an oncoming emergency ambulance.
    """

    def __init__(self, station_id: int = config.STATION_ID):
        self.station_id = station_id
        self._sequence_number: int = 1
        self._history_traces: List[Dict[str, Any]] = []

    def build(
        self,
        data: VehicleData,
        is_cancellation: bool = False,
        validity_duration_sec: int = 20,
    ) -> Dict[str, Any]:
        """
        Build an ETSI ITS DENM structure for emergency vehicle alert.
        Increment sequence number per event.
        """
        now_ms = get_its_timestamp_ms(data.timestamp)
        seq = self._sequence_number
        if not is_cancellation:
            self._sequence_number += 1

        sub_cause = (
            config.SUB_CAUSE_CODE_STATIONARY
            if data.speed_kmh < 1.0
            else config.SUB_CAUSE_CODE_APPROACHING_MOBILE
        )

        lat_etsi = convert_lat_lon_to_etsi(data.latitude, is_lat=True)
        lon_etsi = convert_lat_lon_to_etsi(data.longitude, is_lat=False)
        alt_etsi = int(round(data.altitude * config.ALTITUDE_SCALE_FACTOR))
        speed_etsi = convert_speed_to_etsi(data.speed_kmh)
        heading_etsi = convert_heading_to_etsi(data.heading)

        # Keep a brief trace history for location container
        trace_point = {
            "pathPosition": {
                "deltaLatitude": 0,
                "deltaLongitude": 0,
                "deltaAltitude": 0,
            },
            "pathDeltaTime": 100,
        }

        denm_message: Dict[str, Any] = {
            "header": {
                "protocolVersion": config.ETSI_PROTOCOL_VERSION,
                "messageID": config.MESSAGE_ID_DENM,
                "stationID": data.station_id,
            },
            "denm": {
                "management": {
                    "actionID": {
                        "originatingStationID": data.station_id,
                        "sequenceNumber": seq,
                    },
                    "detectionTime": now_ms,
                    "referenceTime": now_ms,
                    "termination": "isCanceled" if is_cancellation else None,
                    "eventPosition": {
                        "latitude": lat_etsi,
                        "longitude": lon_etsi,
                        "altitude": alt_etsi,
                    },
                    "relevanceDistance": "lessThan1000m",
                    "relevanceTrafficDirection": "allTrafficDirections",
                    "validityDuration": validity_duration_sec,
                    "stationType": config.STATION_TYPE_SPECIAL_VEHICLE,
                },
                "situation": {
                    "informationQuality": 3,
                    "eventType": {
                        "causeCode": config.CAUSE_CODE_EMERGENCY_VEHICLE,
                        "subCauseCode": sub_cause,
                    },
                },
                "location": {
                    "eventSpeed": speed_etsi,
                    "eventPositionHeading": heading_etsi,
                    "traces": [trace_point],
                },
            },
        }

        return denm_message
