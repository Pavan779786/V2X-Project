"""
RSU Client Module
Manages hybrid communication with Roadside Units (RSU):
- Fast UDP datagram transport for continuous 10Hz CAM broadcasting.
- Reliable TCP stream transport for critical DENM emergency notifications.
Tracks packet statistics, throughput, and round-trip transmission latency.
"""

import time
import socket
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Tuple

import config
from monitoring.logger import get_logger

logger = get_logger("rsu_client")


@dataclass
class ClientStatistics:
    """RSU communication metrics."""
    cam_sent: int = 0
    cam_failed: int = 0
    denm_sent: int = 0
    denm_failed: int = 0
    heartbeats_sent: int = 0
    total_bytes_sent: int = 0
    last_cam_timestamp: Optional[datetime] = None
    last_denm_timestamp: Optional[datetime] = None
    last_ack_timestamp: Optional[datetime] = None
    last_latency_ms: float = 0.0
    rsu_connected: bool = False


class RSUClient:
    """
    Dual-protocol client for low-latency V2X broadcast and high-reliability alert messaging.
    """

    def __init__(
        self,
        rsu_host: str = config.RSU_HOST,
        udp_port: int = config.RSU_UDP_PORT,
        tcp_port: int = config.RSU_TCP_PORT,
        timeout: float = config.SOCKET_TIMEOUT_SEC,
    ):
        self.rsu_host = rsu_host
        self.udp_port = udp_port
        self.tcp_port = tcp_port
        self.timeout = timeout

        self.stats = ClientStatistics()
        self._udp_sock: Optional[socket.socket] = None
        self._tcp_reader: Optional[asyncio.StreamReader] = None
        self._tcp_writer: Optional[asyncio.StreamWriter] = None
        self._tcp_lock = asyncio.Lock()
        self._is_running = False

    async def start(self) -> None:
        """Initialize sockets and communication channels."""
        self._is_running = True
        self._init_udp_socket()
        await self._ensure_tcp_connected()
        logger.info(f"RSUClient initialized for target {self.rsu_host} (UDP:{self.udp_port}, TCP:{self.tcp_port})")

    async def stop(self) -> None:
        """Close UDP and TCP connections cleanly."""
        self._is_running = False
        if self._udp_sock:
            try:
                self._udp_sock.close()
            except Exception as e:
                logger.debug(f"Error closing UDP socket: {e}")
            self._udp_sock = None

        await self._close_tcp()
        logger.info("RSUClient stopped")

    def _init_udp_socket(self) -> None:
        """Create non-blocking UDP socket configured for broadcast/unicast."""
        try:
            self._udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self._udp_sock.setblocking(False)
        except Exception as e:
            logger.error(f"Failed to create UDP socket: {e}", exc_info=True)

    async def _ensure_tcp_connected(self) -> bool:
        """Establish or verify TCP connection to RSU."""
        if self._tcp_writer and not self._tcp_writer.is_closing():
            return True

        async with self._tcp_lock:
            # Re-check inside lock
            if self._tcp_writer and not self._tcp_writer.is_closing():
                return True

            try:
                logger.debug(f"Attempting TCP connection to {self.rsu_host}:{self.tcp_port}...")
                # Short connect timeout so emergency DENM is not delayed if RSU is offline
                connect_timeout = min(0.3, self.timeout)
                conn_coro = asyncio.open_connection(self.rsu_host, self.tcp_port)
                reader, writer = await asyncio.wait_for(conn_coro, timeout=connect_timeout)
                self._tcp_reader = reader
                self._tcp_writer = writer
                self.stats.rsu_connected = True
                logger.info(f"TCP connection established to RSU at {self.rsu_host}:{self.tcp_port}")
                return True
            except (asyncio.TimeoutError, ConnectionRefusedError, OSError) as e:
                logger.debug(f"RSU TCP connection unavailable: {e}")
                self.stats.rsu_connected = False
                self._tcp_reader = None
                self._tcp_writer = None
                return False

    async def _close_tcp(self) -> None:
        """Safely close TCP writer and reader streams."""
        async with self._tcp_lock:
            if self._tcp_writer:
                try:
                    self._tcp_writer.close()
                    await self._tcp_writer.wait_closed()
                except Exception as e:
                    logger.debug(f"Error closing TCP stream: {e}")
                finally:
                    self._tcp_writer = None
                    self._tcp_reader = None
                    self.stats.rsu_connected = False

    async def send_cam(self, framed_data: bytes) -> bool:
        """
        Send CAM packet to RSU over fast UDP.
        Returns True if socket send succeeded, False otherwise.
        """
        if not self._udp_sock:
            self._init_udp_socket()

        t0 = time.monotonic()
        try:
            loop = asyncio.get_event_loop()
            await loop.sock_sendto(self._udp_sock, framed_data, (self.rsu_host, self.udp_port))

            latency = (time.monotonic() - t0) * 1000.0
            self.stats.cam_sent += 1
            self.stats.total_bytes_sent += len(framed_data)
            self.stats.last_cam_timestamp = datetime.now(timezone.utc)
            self.stats.last_latency_ms = round(latency, 2)
            return True
        except Exception as e:
            self.stats.cam_failed += 1
            logger.debug(f"UDP CAM transmit failed: {e}")
            return False

    async def send_denm(self, framed_data: bytes) -> bool:
        """
        Send DENM alert over TCP with guaranteed delivery.
        Falls back to UDP if TCP is temporarily down.
        """
        t0 = time.monotonic()
        connected = await self._ensure_tcp_connected()

        if connected and self._tcp_writer:
            try:
                async with self._tcp_lock:
                    self._tcp_writer.write(framed_data)
                    await asyncio.wait_for(self._tcp_writer.drain(), timeout=self.timeout)

                latency = (time.monotonic() - t0) * 1000.0
                self.stats.denm_sent += 1
                self.stats.total_bytes_sent += len(framed_data)
                self.stats.last_denm_timestamp = datetime.now(timezone.utc)
                self.stats.last_latency_ms = round(latency, 2)
                logger.info(f"DENM successfully delivered to RSU via TCP ({len(framed_data)} bytes)")
                return True
            except Exception as e:
                logger.warning(f"TCP DENM transmit error: {e}. Falling back to UDP broadcast.")
                await self._close_tcp()

        # Fallback to UDP if TCP is not connected
        try:
            loop = asyncio.get_event_loop()
            await loop.sock_sendto(self._udp_sock, framed_data, (self.rsu_host, self.udp_port))
            self.stats.denm_sent += 1
            self.stats.total_bytes_sent += len(framed_data)
            self.stats.last_denm_timestamp = datetime.now(timezone.utc)
            logger.info("DENM delivered to RSU via UDP fallback")
            return True
        except Exception as e:
            self.stats.denm_failed += 1
            logger.error(f"Both TCP and UDP failed for DENM transmission: {e}")
            return False

    async def send_heartbeat(self, framed_data: bytes) -> bool:
        """Send periodic heartbeat packet to RSU via UDP."""
        if not self._udp_sock:
            self._init_udp_socket()

        try:
            loop = asyncio.get_event_loop()
            await loop.sock_sendto(self._udp_sock, framed_data, (self.rsu_host, self.udp_port))
            self.stats.heartbeats_sent += 1
            self.stats.total_bytes_sent += len(framed_data)
            return True
        except Exception as e:
            logger.debug(f"Heartbeat send failed: {e}")
            return False
