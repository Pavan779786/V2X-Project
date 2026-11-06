"""
Unit Tests for Wire Protocol Handler and RSU Client
Validates framing, CRC32 integrity checks, ACK processing, and socket operations.
"""

import zlib
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import config
from communication.protocol_handler import ProtocolHandler, V2XFrame
from communication.rsu_client import RSUClient


def test_protocol_handler_pack_unpack_frame():
    handler = ProtocolHandler()
    payload = b'{"test": "etsi_cam_data", "station": 1042}'

    framed = handler.pack_frame(msg_type=config.MSG_TYPE_CAM, payload=payload, seq_num=42)

    # Header is 22 bytes + payload (39 bytes) + CRC (4 bytes) = 65 bytes
    assert len(framed) == 22 + len(payload) + 4

    unpacked = handler.unpack_frame(framed)
    assert unpacked.magic == config.WIRE_MAGIC
    assert unpacked.version == config.WIRE_VERSION
    assert unpacked.msg_type == config.MSG_TYPE_CAM
    assert unpacked.seq_num == 42
    assert unpacked.payload == payload
    assert unpacked.is_cam is True


def test_protocol_handler_crc_integrity_detection():
    handler = ProtocolHandler()
    payload = b'critical_safety_denm_alert'
    framed = bytearray(handler.pack_frame(msg_type=config.MSG_TYPE_DENM, payload=payload))

    # Tamper with single byte in payload
    framed[25] = (framed[25] ^ 0xFF)

    with pytest.raises(ValueError, match="CRC32 mismatch"):
        handler.unpack_frame(bytes(framed))


def test_protocol_handler_frame_underflow():
    handler = ProtocolHandler()
    too_short = b"V2X1\x01\x01"

    with pytest.raises(ValueError, match="Frame underflow"):
        handler.unpack_frame(too_short)


def test_protocol_handler_ack_roundtrip():
    handler = ProtocolHandler()
    ack_frame_bytes = handler.pack_ack(ack_seq=105, status_code=0, station_id=9999)
    unpacked_frame = handler.unpack_frame(ack_frame_bytes)

    assert unpacked_frame.is_ack is True
    parsed_ack = handler.parse_ack(unpacked_frame)
    assert parsed_ack.ack_seq_num == 105
    assert parsed_ack.status_code == 0
    assert parsed_ack.rsu_id == 9999


@pytest.mark.asyncio
async def test_rsu_client_send_cam_mocked():
    client = RSUClient(rsu_host="127.0.0.1", udp_port=5900)
    test_packet = b"MOCK_FRAMED_CAM_PACKET_BYTES"

    # Mock loop.sock_sendto to avoid network reliance
    with patch("asyncio.get_event_loop") as mock_get_loop:
        mock_loop = MagicMock()
        mock_loop.sock_sendto = AsyncMock(return_value=len(test_packet))
        mock_get_loop.return_value = mock_loop

        client._init_udp_socket()
        success = await client.send_cam(test_packet)

        assert success is True
        assert client.stats.cam_sent == 1
        assert client.stats.total_bytes_sent == len(test_packet)
        assert client.stats.last_cam_timestamp is not None


@pytest.mark.asyncio
async def test_rsu_client_send_denm_fallback_to_udp():
    client = RSUClient(rsu_host="127.0.0.1", udp_port=5900, tcp_port=5901)
    test_packet = b"MOCK_FRAMED_DENM_PACKET_BYTES"

    # Simulate TCP down, fallback to UDP
    with patch.object(client, "_ensure_tcp_connected", AsyncMock(return_value=False)):
        with patch("asyncio.get_event_loop") as mock_get_loop:
            mock_loop = MagicMock()
            mock_loop.sock_sendto = AsyncMock(return_value=len(test_packet))
            mock_get_loop.return_value = mock_loop

            client._init_udp_socket()
            success = await client.send_denm(test_packet)

            assert success is True
            assert client.stats.denm_sent == 1
            assert client.stats.total_bytes_sent == len(test_packet)
            assert client.stats.last_denm_timestamp is not None
