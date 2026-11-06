"""
Message Serialization Module
Provides dual serialization capabilities:
1. Standard UTF-8 JSON serialization for ETSI ITS CAM and DENM dictionaries.
2. Compact binary struct packing (ASN.1 / UPER inspired) for bandwidth-constrained 802.11p channels.
"""

import json
import struct
from typing import Dict, Any, Tuple, Union

import config
from monitoring.logger import get_logger

logger = get_logger("serializer")

# Compact binary struct layout:
# ! (Network / Big-Endian)
# I  - Station ID (4 bytes)
# Q  - Timestamp ms (8 bytes)
# i  - Latitude ETSI (4 bytes)
# i  - Longitude ETSI (4 bytes)
# i  - Altitude ETSI (4 bytes)
# H  - Speed ETSI (2 bytes)
# H  - Heading ETSI (2 bytes)
# h  - Acceleration ETSI (2 bytes)
# B  - Vehicle Length (1 byte)
# B  - Vehicle Width (1 byte)
# B  - Flags: bit 0=siren, bit 1=lights, bit 2=emergency_mode (1 byte)
# B  - Message Type (1 byte)
# Total = 4 + 8 + 4 + 4 + 4 + 2 + 2 + 2 + 1 + 1 + 1 + 1 = 34 bytes
COMPACT_STRUCT_FORMAT = "!IQiiiHHhBBBB"
COMPACT_STRUCT_SIZE = struct.calcsize(COMPACT_STRUCT_FORMAT)


class MessageSerializer:
    """Serializes and deserializes V2X messages to/from JSON and compact binary formats."""

    @staticmethod
    def to_json_bytes(message_dict: Dict[str, Any]) -> bytes:
        """Serialize a CAM or DENM dictionary to UTF-8 encoded JSON bytes."""
        return json.dumps(message_dict, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def from_json_bytes(data: bytes) -> Dict[str, Any]:
        """Deserialize UTF-8 JSON bytes back to a Python dictionary."""
        return json.loads(data.decode("utf-8"))

    @staticmethod
    def to_compact_binary(
        station_id: int,
        timestamp_ms: int,
        lat_etsi: int,
        lon_etsi: int,
        alt_etsi: int,
        speed_etsi: int,
        heading_etsi: int,
        accel_etsi: int,
        length_val: int,
        width_val: int,
        siren_active: bool,
        lights_active: bool,
        emergency_active: bool,
        msg_type: int = config.MSG_TYPE_CAM,
    ) -> bytes:
        """Serialize core vehicle dynamic telemetry into a compact 34-byte binary payload."""
        flags = 0
        if siren_active:
            flags |= 0x01
        if lights_active:
            flags |= 0x02
        if emergency_active:
            flags |= 0x04

        return struct.pack(
            COMPACT_STRUCT_FORMAT,
            station_id,
            timestamp_ms,
            lat_etsi,
            lon_etsi,
            alt_etsi,
            speed_etsi,
            heading_etsi,
            accel_etsi,
            min(255, length_val),
            min(255, width_val),
            flags,
            msg_type,
        )

    @staticmethod
    def from_compact_binary(data: bytes) -> Dict[str, Any]:
        """Unpack compact binary payload into a dictionary representation."""
        if len(data) < COMPACT_STRUCT_SIZE:
            raise ValueError(
                f"Binary data too short: got {len(data)} bytes, expected {COMPACT_STRUCT_SIZE}"
            )

        unpacked = struct.unpack(COMPACT_STRUCT_FORMAT, data[:COMPACT_STRUCT_SIZE])
        flags = unpacked[10]

        return {
            "station_id": unpacked[0],
            "timestamp_ms": unpacked[1],
            "latitude_etsi": unpacked[2],
            "longitude_etsi": unpacked[3],
            "altitude_etsi": unpacked[4],
            "speed_etsi": unpacked[5],
            "heading_etsi": unpacked[6],
            "acceleration_etsi": unpacked[7],
            "vehicle_length": unpacked[8],
            "vehicle_width": unpacked[9],
            "siren_active": bool(flags & 0x01),
            "lights_active": bool(flags & 0x02),
            "emergency_active": bool(flags & 0x04),
            "msg_type": unpacked[11],
        }
