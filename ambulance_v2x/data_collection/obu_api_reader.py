"""
OBU Management API Reader Module
Communicates with the On-Board Unit's internal management REST API
and WebSocket stream to obtain link health, RF statistics, and V2X stack state.
Falls back seamlessly to realistic simulation if the OBU REST interface is unreachable.
"""

import json
import asyncio
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

import config
from monitoring.logger import get_logger

logger = get_logger("obu_api_reader")


@dataclass
class NeighborRSU:
    """Discovered Roadside Unit information."""
    rsu_id: str
    rssi_dbm: float
    distance_m: float
    channel: int


@dataclass
class OBUTelemetry:
    """Telemetry report from the OBU V2X radio modem and stack."""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    link_quality: float = 1.0  # 0.0 to 1.0
    rssi_dbm: float = -65.0
    snr_db: float = 24.5
    packet_error_rate: float = 0.01
    channel_utilization_pct: float = 14.2
    tx_packets: int = 0
    rx_packets: int = 0
    gnss_sync: bool = True
    v2x_stack_active: bool = True
    neighbors: List[NeighborRSU] = field(default_factory=list)
    connected: bool = True
    source: str = "uninitialized"


class OBUApiReader:
    """
    OBU interface client providing real-time RF health metrics,
    RSU neighbor discovery, and modem status.
    """

    def __init__(self, simulation_mode: bool = config.SIMULATION_MODE):
        self.simulation_mode = simulation_mode
        self._lock = threading.Lock()
        self._latest_data = OBUTelemetry(source="init")
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._session: Optional[Any] = None
        self._consecutive_failures: int = 0

    async def start(self) -> None:
        """Start the OBU polling loop."""
        if self._running:
            return
        self._running = True

        if HAS_AIOHTTP:
            timeout = aiohttp.ClientTimeout(total=config.OBU_REQUEST_TIMEOUT_SEC)
            self._session = aiohttp.ClientSession(timeout=timeout)

        logger.info(f"OBUApiReader started in {'SIMULATION' if self.simulation_mode else 'REST_API'} mode")
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop polling and close HTTP session."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        if self._session and not self._session.closed:
            await self._session.close()

        logger.info("OBUApiReader stopped")

    def get_latest_data(self) -> OBUTelemetry:
        """Thread-safe retrieval of latest OBU status."""
        with self._lock:
            return self._latest_data

    async def _run_loop(self) -> None:
        """Periodic query loop running at 1Hz."""
        while self._running:
            try:
                if self.simulation_mode:
                    self._step_simulation()
                else:
                    success = await self._fetch_obu_rest()
                    if not success:
                        self._consecutive_failures += 1
                        # Fallback after 3 failures
                        if self._consecutive_failures >= 3:
                            self._step_simulation()
                    else:
                        self._consecutive_failures = 0

                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in OBUApiReader loop: {e}", exc_info=True)
                await asyncio.sleep(2.0)

    def _step_simulation(self) -> None:
        """Simulate realistic OBU RF modem statistics and nearby RSUs."""
        with self._lock:
            tx_count = self._latest_data.tx_packets + 10
            rx_count = self._latest_data.rx_packets + 2

            neighbors = [
                NeighborRSU(
                    rsu_id=f"RSU-BERLIN-{config.RSU_HOST[-3:] if len(config.RSU_HOST) >= 3 else '01'}",
                    rssi_dbm=-62.5,
                    distance_m=120.0,
                    channel=180,  # 5.9 GHz ITS-G5 channel
                )
            ]

            self._latest_data = OBUTelemetry(
                timestamp=datetime.now(timezone.utc),
                link_quality=0.95,
                rssi_dbm=-62.5,
                snr_db=26.0,
                packet_error_rate=0.005,
                channel_utilization_pct=16.8,
                tx_packets=tx_count,
                rx_packets=rx_count,
                gnss_sync=True,
                v2x_stack_active=True,
                neighbors=neighbors,
                connected=True,
                source="simulated",
            )

    async def _fetch_obu_rest(self) -> bool:
        """Perform REST request to local OBU management interface."""
        if not HAS_AIOHTTP or not self._session:
            return False

        try:
            url = f"{config.OBU_API_BASE_URL}/status"
            async with self._session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    with self._lock:
                        self._latest_data = OBUTelemetry(
                            timestamp=datetime.now(timezone.utc),
                            link_quality=float(data.get("link_quality", 0.9)),
                            rssi_dbm=float(data.get("rssi_dbm", -70.0)),
                            snr_db=float(data.get("snr_db", 20.0)),
                            packet_error_rate=float(data.get("per", 0.01)),
                            channel_utilization_pct=float(data.get("chan_util", 15.0)),
                            tx_packets=int(data.get("tx_packets", 0)),
                            rx_packets=int(data.get("rx_packets", 0)),
                            gnss_sync=bool(data.get("gnss_sync", True)),
                            v2x_stack_active=bool(data.get("v2x_active", True)),
                            neighbors=[
                                NeighborRSU(
                                    rsu_id=n.get("id", "RSU-UNKNOWN"),
                                    rssi_dbm=n.get("rssi", -75.0),
                                    distance_m=n.get("distance", 300.0),
                                    channel=n.get("channel", 180),
                                )
                                for n in data.get("neighbors", [])
                            ],
                            connected=True,
                            source="obu_rest",
                        )
                    return True
                else:
                    logger.debug(f"OBU REST query returned HTTP {response.status}")
        except Exception as ex:
            logger.debug(f"OBU REST query error: {ex}")
        return False
