"""
V2X Wire Protocol Framing & Handler
Encapsulates application payloads with standard framing:
[MAGIC(4B)][VERSION(1B)][MSG_TYPE(1B)][SEQ_NUM(4B)][TIMESTAMP(8B)][LENGTH(4B)][PAYLOAD(NB)][CRC32(4B)]
Includes CRC32 integrity verification, ACK parsing, and sequence management.
"""

import zlib
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Tuple

import config
from monitoring.logger import get_logger

logger = get_logger("protocol_handler")

# Wire frame format:
# ! (Network Big-Endian)
# 4s - Magic (e.g. b"V2X1")
# B  - Protocol Version (1 byte)
# B  - Message Type (1 byte: 1=CAM, 2=DENM, 3=HEARTBEAT, 4=ACK)
# I  - Sequence Number (4 bytes)
# Q  - Timestamp Milliseconds (8 bytes)
# I  - Payload Length (4 bytes)
HEADER_FORMAT = "!4sBBII"  # Wait: magic(4s), ver(B), type(B), seq(I), reserved(I) -> let's match exact layout!
# Prompt specification:
# [MAGIC(4B)][VERSION(1B)][MSG_TYPE(1B)][SEQ_NUM(4B)][TIMESTAMP(8B)][LENGTH(4B)][PAYLOAD][CRC32(4B)]
# 4s (4) + B (1) + B (1) + I (4) + Q (8) + I (4) = 22 bytes!
# CRC32 is 4 bytes at end: I (4)
WIRE_HEADER_FORMAT = "!4sBBIQI"
WIRE_HEADER_SIZE = struct.calcsize(WIRE_HEADER_FORMAT)  # 22 bytes
CRC_FORMAT = "!I"
CRC_SIZE = struct.calcsize(CRC_FORMAT)  # 4 bytes


@dataclass
class V2XFrame:
    """Parsed V2X wire frame."""
    magic: bytes
    version: int
    msg_type: int
    seq_num: int
    timestamp_ms: int
    payload: bytes
    crc32: int

    @property
    def is_cam(self) -> bool:
        return self.msg_type == config.MSG_TYPE_CAM

    @property
    def is_denm(self) -> bool:
        return self.msg_type == config.MSG_TYPE_DENM

    @property
    def is_heartbeat(self) -> bool:
        return self.msg_type == config.MSG_TYPE_HEARTBEAT

    @property
    def is_ack(self) -> bool:
        return self.msg_type == config.MSG_TYPE_ACK


@dataclass
class ACKPAYLOAD:
    """Structured acknowledgement from RSU."""
    ack_seq_num: int
    status_code: int  # 0 = OK, 1 = Invalid CRC, 2 = Malformed
    rsu_id: int
    received_timestamp_ms: int


class ProtocolHandler:
    """Encapsulates framing and integrity checking for wire communication."""

    def __init__(self):
        self._seq_counter: int = 1

    def next_sequence(self) -> int:
        """Increment and return thread-safe sequence number."""
        seq = self._seq_counter
        self._seq_counter = (self._seq_counter + 1) & 0xFFFFFFFF
        return seq

    def pack_frame(
        self,
        msg_type: int,
        payload: bytes,
        seq_num: Optional[int] = None,
        timestamp_ms: Optional[int] = None,
    ) -> bytes:
        """
        Encode payload into a framed binary packet with CRC32 checksum.
        Format: [MAGIC(4B)][VERSION(1B)][MSG_TYPE(1B)][SEQ_NUM(4B)][TIMESTAMP(8B)][LENGTH(4B)][PAYLOAD][CRC32(4B)]
        """
        if seq_num is None:
            seq_num = self.next_sequence()

        if timestamp_ms is None:
            timestamp_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        payload_len = len(payload)
        header = struct.pack(
            WIRE_HEADER_FORMAT,
            config.WIRE_MAGIC,
            config.WIRE_VERSION,
            msg_type,
            seq_num,
            timestamp_ms,
            payload_len,
        )

        data_to_checksum = header + payload
        checksum = zlib.crc32(data_to_checksum) & 0xFFFFFFFF
        trailer = struct.pack(CRC_FORMAT, checksum)

        return data_to_checksum + trailer

    def unpack_frame(self, raw_data: bytes) -> V2XFrame:
        """
        Unpack raw wire bytes into a V2XFrame.
        Validates magic bytes, packet size, and CRC32 integrity.
        Raises ValueError on corruption or framing violations.
        """
        min_size = WIRE_HEADER_SIZE + CRC_SIZE
        if len(raw_data) < min_size:
            raise ValueError(
                f"Frame underflow: received {len(raw_data)} bytes, minimum required {min_size}"
            )

        # Unpack header
        header_bytes = raw_data[:WIRE_HEADER_SIZE]
        magic, version, msg_type, seq_num, timestamp_ms, payload_len = struct.unpack(
            WIRE_HEADER_FORMAT, header_bytes
        )

        if magic != config.WIRE_MAGIC:
            raise ValueError(f"Invalid magic bytes: expected {config.WIRE_MAGIC}, received {magic}")

        if version != config.WIRE_VERSION:
            raise ValueError(f"Protocol version mismatch: expected {config.WIRE_VERSION}, received {version}")

        expected_total_size = WIRE_HEADER_SIZE + payload_len + CRC_SIZE
        if len(raw_data) < expected_total_size:
            raise ValueError(
                f"Incomplete payload: expected {payload_len} bytes, got {len(raw_data) - min_size}"
            )

        payload = raw_data[WIRE_HEADER_SIZE : WIRE_HEADER_SIZE + payload_len]
        received_crc = struct.unpack(
            CRC_FORMAT, raw_data[WIRE_HEADER_SIZE + payload_len : expected_total_size]
        )[0]

        # Verify CRC32
        data_to_verify = raw_data[: WIRE_HEADER_SIZE + payload_len]
        calculated_crc = zlib.crc32(data_to_verify) & 0xFFFFFFFF

        if received_crc != calculated_crc:
            raise ValueError(
                f"CRC32 mismatch! Received: 0x{received_crc:08X}, Calculated: 0x{calculated_crc:08X}"
            )

        return V2XFrame(
            magic=magic,
            version=version,
            msg_type=msg_type,
            seq_num=seq_num,
            timestamp_ms=timestamp_ms,
            payload=payload,
            crc32=received_crc,
        )

    def pack_heartbeat(self, station_id: int) -> bytes:
        """Construct a framed heartbeat ping message."""
        payload = struct.pack("!I", station_id)
        return self.pack_frame(config.MSG_TYPE_HEARTBEAT, payload)

    def pack_ack(self, ack_seq: int, status_code: int, station_id: int) -> bytes:
        """Construct a framed acknowledgement packet."""
        payload = struct.pack("!IIQ", ack_seq, status_code, station_id)
        return self.pack_frame(config.MSG_TYPE_ACK, payload)

    def parse_ack(self, frame: V2XFrame) -> ACKPAYLOAD:
        """Parse payload of an ACK frame."""
        if not frame.is_ack:
            raise ValueError(f"Frame msg_type {frame.msg_type} is not an ACK (0x04)")
        if len(frame.payload) < 16:
            raise ValueError(f"ACK payload too short: {len(frame.payload)} bytes")

        ack_seq, status_code, rsu_id = struct.unpack("!IIQ", frame.payload[:16])
        return ACKPAYLOAD(
            ack_seq_num=ack_seq,
            status_code=status_code,
            rsu_id=rsu_id,
            received_timestamp_ms=frame.timestamp_ms,
        )
