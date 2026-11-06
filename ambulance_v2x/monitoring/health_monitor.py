"""
System Health Monitor Module
Continuously inspects sensor health, bus connectivity, link quality,
and classifies overall system readiness into HEALTHY, DEGRADED, or CRITICAL.
"""

from __future__ import annotations
import asyncio
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Callable, TYPE_CHECKING

import config
from monitoring.logger import get_logger

if TYPE_CHECKING:
    from data_collection.data_aggregator import DataAggregator
    from communication.connection_manager import ConnectionManager

logger = get_logger("health_monitor")


class HealthStatus(str, Enum):
    """System and component health severity levels."""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"


@dataclass
class ComponentHealth:
    """Diagnostic state for an individual subsystem."""
    name: str
    status: HealthStatus = HealthStatus.HEALTHY
    details: str = "Nominal"
    last_ok_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class HealthMonitor:
    """
    Supervises system invariants:
    - GPS validity, satellite locks, and coordinates freshness
    - CAN interface activity and packet rates
    - RSU communication link status and round-trip health
    """

    def __init__(
        self,
        aggregator: DataAggregator,
        connection_manager: ConnectionManager,
    ):
        self.aggregator = aggregator
        self.connection_manager = connection_manager

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._overall_status = HealthStatus.HEALTHY
        self._components: Dict[str, ComponentHealth] = {
            "GPS": ComponentHealth(name="GPS"),
            "CAN": ComponentHealth(name="CAN"),
            "RSU_LINK": ComponentHealth(name="RSU_LINK"),
            "OBU_MODEM": ComponentHealth(name="OBU_MODEM"),
        }

        self._status_change_callbacks: List[Callable[[HealthStatus, HealthStatus], None]] = []

    @property
    def overall_status(self) -> HealthStatus:
        return self._overall_status

    @property
    def components(self) -> Dict[str, ComponentHealth]:
        return self._components

    def register_transition_callback(self, cb: Callable[[HealthStatus, HealthStatus], None]) -> None:
        self._status_change_callbacks.append(cb)

    async def start(self) -> None:
        """Start the periodic health audit loop."""
        if self._running:
            return
        self._running = True
        logger.info("HealthMonitor audit engine started")
        self._task = asyncio.create_task(self._audit_loop())

    async def stop(self) -> None:
        """Stop the audit loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("HealthMonitor stopped")

    async def _audit_loop(self) -> None:
        """Audit subsystems periodically."""
        while self._running:
            try:
                self._evaluate_health()
                await asyncio.sleep(config.HEALTH_CHECK_INTERVAL_SEC)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error evaluating system health: {e}", exc_info=True)
                await asyncio.sleep(2.0)

    def _evaluate_health(self) -> None:
        """Inspect telemetry snapshots and update diagnostic statuses."""
        now = datetime.now(timezone.utc)
        vehicle_data = self.aggregator.get_latest_vehicle_data()

        # 1. Audit GPS Subsystem
        gps_comp = self._components["GPS"]
        gps_age = (now - vehicle_data.timestamp).total_seconds()

        if vehicle_data.gps_fix_quality == 0 or (vehicle_data.latitude == 0.0 and vehicle_data.longitude == 0.0):
            gps_comp.status = HealthStatus.CRITICAL
            gps_comp.details = "No GPS fix / Null coordinates"
        elif gps_age > config.GPS_HEALTH_TIMEOUT_SEC:
            gps_comp.status = HealthStatus.DEGRADED
            gps_comp.details = f"GPS fix stale ({gps_age:.1f}s old)"
        elif vehicle_data.satellite_count < 4 or vehicle_data.hdop > 4.0:
            gps_comp.status = HealthStatus.DEGRADED
            gps_comp.details = f"Poor geometry: Sats={vehicle_data.satellite_count}, HDOP={vehicle_data.hdop:.1f}"
            gps_comp.last_ok_timestamp = now
        else:
            gps_comp.status = HealthStatus.HEALTHY
            gps_comp.details = f"Lock OK: Sats={vehicle_data.satellite_count}, HDOP={vehicle_data.hdop:.1f}"
            gps_comp.last_ok_timestamp = now

        # 2. Audit CAN Bus Subsystem
        can_comp = self._components["CAN"]
        if not vehicle_data.can_connected:
            can_comp.status = HealthStatus.DEGRADED
            can_comp.details = "CAN bus disconnected (using GPS fallback)"
        else:
            can_comp.status = HealthStatus.HEALTHY
            can_comp.details = "Bus connected; telemetry synchronized"
            can_comp.last_ok_timestamp = now

        # 3. Audit RSU Link Subsystem
        rsu_comp = self._components["RSU_LINK"]
        if self.connection_manager.is_link_alive:
            rsu_comp.status = HealthStatus.HEALTHY
            rsu_comp.details = "RSU heartbeat responsive"
            rsu_comp.last_ok_timestamp = now
        else:
            rsu_comp.status = HealthStatus.DEGRADED
            rsu_comp.details = "RSU unreachable or missed heartbeats"

        # 4. Audit OBU Modem Subsystem
        obu_comp = self._components["OBU_MODEM"]
        if vehicle_data.obu_link_quality > 0.7:
            obu_comp.status = HealthStatus.HEALTHY
            obu_comp.details = f"Link quality {int(vehicle_data.obu_link_quality * 100)}%"
            obu_comp.last_ok_timestamp = now
        elif vehicle_data.obu_link_quality > 0.3:
            obu_comp.status = HealthStatus.DEGRADED
            obu_comp.details = f"Low link quality {int(vehicle_data.obu_link_quality * 100)}%"
        else:
            obu_comp.status = HealthStatus.CRITICAL
            obu_comp.details = "OBU modem RF link loss"

        # 5. Compute Consolidated System Health
        critical_count = sum(1 for c in self._components.values() if c.status == HealthStatus.CRITICAL)
        degraded_count = sum(1 for c in self._components.values() if c.status == HealthStatus.DEGRADED)

        if critical_count > 0:
            new_overall = HealthStatus.CRITICAL
        elif degraded_count > 0:
            new_overall = HealthStatus.DEGRADED
        else:
            new_overall = HealthStatus.HEALTHY

        if new_overall != self._overall_status:
            old_status = self._overall_status
            self._overall_status = new_overall
            logger.warning(f"SYSTEM HEALTH TRANSITION: {old_status} -> {new_overall}")
            for cb in self._status_change_callbacks:
                try:
                    cb(old_status, new_overall)
                except Exception as ex:
                    logger.error(f"Error in health transition callback: {ex}")
