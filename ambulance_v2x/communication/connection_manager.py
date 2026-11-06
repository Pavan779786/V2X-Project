"""
RSU Connection Manager Module
Supervises communication health with the RSU:
- 1 Hz periodic heartbeat loop.
- Exponential backoff TCP reconnection engine (1s -> 2s -> 4s -> max 30s).
- Link loss and restoration detection with state transition callbacks.
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional, Callable, List

import config
from monitoring.logger import get_logger
from .protocol_handler import ProtocolHandler
from .rsu_client import RSUClient

logger = get_logger("connection_manager")


class ConnectionManager:
    """
    Supervises link health, triggers heartbeat transmissions,
    and runs self-healing reconnection loops.
    """

    def __init__(
        self,
        rsu_client: RSUClient,
        protocol_handler: ProtocolHandler,
        station_id: int = config.STATION_ID,
    ):
        self.rsu_client = rsu_client
        self.protocol_handler = protocol_handler
        self.station_id = station_id

        self._running = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._link_alive = False
        self._consecutive_missed_beats = 0
        self._reconnect_delay = config.TCP_RECONNECT_INITIAL_DELAY

        # Callbacks for status changes
        self._on_link_lost_callbacks: List[Callable[[], None]] = []
        self._on_link_restored_callbacks: List[Callable[[], None]] = []

    @property
    def is_link_alive(self) -> bool:
        """Indicates if RSU heartbeat/link is currently considered operational."""
        return self._link_alive

    def register_link_lost_callback(self, cb: Callable[[], None]) -> None:
        self._on_link_lost_callbacks.append(cb)

    def register_link_restored_callback(self, cb: Callable[[], None]) -> None:
        self._on_link_restored_callbacks.append(cb)

    async def start(self) -> None:
        """Start heartbeat generator and connection maintenance tasks."""
        if self._running:
            return
        self._running = True
        logger.info("ConnectionManager started")
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def stop(self) -> None:
        """Cancel background supervision tasks."""
        self._running = False
        for task in (self._heartbeat_task, self._reconnect_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        logger.info("ConnectionManager stopped")

    async def _heartbeat_loop(self) -> None:
        """Emit framed heartbeat at 1Hz to target RSU."""
        while self._running:
            try:
                heartbeat_frame = self.protocol_handler.pack_heartbeat(self.station_id)
                success = await self.rsu_client.send_heartbeat(heartbeat_frame)

                if success:
                    if not self._link_alive:
                        self._link_alive = True
                        logger.info("RSU communication link ACTIVE / RESTORED")
                        for cb in self._on_link_restored_callbacks:
                            try:
                                cb()
                            except Exception as ex:
                                logger.error(f"Error in link_restored callback: {ex}")
                    self._consecutive_missed_beats = 0
                else:
                    self._consecutive_missed_beats += 1
                    if self._consecutive_missed_beats >= 3 and self._link_alive:
                        self._link_alive = False
                        logger.warning(
                            f"RSU Link DEGRADED: {self._consecutive_missed_beats} consecutive missed heartbeats"
                        )
                        for cb in self._on_link_lost_callbacks:
                            try:
                                cb()
                            except Exception as ex:
                                logger.error(f"Error in link_lost callback: {ex}")

                await asyncio.sleep(config.HEARTBEAT_INTERVAL_SEC)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Exception in heartbeat loop: {e}", exc_info=True)
                await asyncio.sleep(1.0)

    async def _reconnect_loop(self) -> None:
        """Backoff loop attempting TCP reconnects when severed."""
        while self._running:
            try:
                # If TCP writer is closed, attempt reconnect
                if not self.rsu_client.stats.rsu_connected:
                    connected = await self.rsu_client._ensure_tcp_connected()
                    if connected:
                        logger.info("TCP socket successfully re-established with RSU")
                        self._reconnect_delay = config.TCP_RECONNECT_INITIAL_DELAY
                    else:
                        # Exponential backoff
                        self._reconnect_delay = min(
                            self._reconnect_delay * config.TCP_RECONNECT_BACKOFF_FACTOR,
                            config.TCP_RECONNECT_MAX_DELAY,
                        )
                        logger.debug(f"RSU TCP reconnect failed. Retrying in {self._reconnect_delay:.1f}s")
                else:
                    self._reconnect_delay = config.TCP_RECONNECT_INITIAL_DELAY

                await asyncio.sleep(self._reconnect_delay)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Exception in TCP reconnect loop: {e}", exc_info=True)
                await asyncio.sleep(2.0)
