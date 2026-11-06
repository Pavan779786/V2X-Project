"""
Data Aggregator Module
Fuses telemetry from GPS, CAN bus, and OBU modem into a unified VehicleData model.
Implements sensor priority, validation freshness checks, and 10Hz continuous fusion.
"""

import asyncio
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import config
from monitoring.logger import get_logger
from .gps_reader import GPSReader, GPSData
from .can_reader import CANReader, CANData
from .obu_api_reader import OBUApiReader, OBUTelemetry

logger = get_logger("data_aggregator")


@dataclass
class VehicleData:
    """Unified vehicle state model used across message processing and transmission."""
    station_id: int = config.STATION_ID
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0
    speed_kmh: float = 0.0
    heading: float = 0.0
    acceleration: float = 0.0
    vehicle_width: int = config.VEHICLE_WIDTH_CM
    vehicle_length: int = config.VEHICLE_LENGTH_CM
    emergency_active: bool = False
    siren_active: bool = False
    lights_active: bool = False
    gps_fix_quality: int = 0
    satellite_count: int = 0
    hdop: float = 99.9
    can_connected: bool = False
    obu_link_quality: float = 0.0


class DataAggregator:
    """
    Subsystem coordinator that samples GPS, CAN, and OBU interfaces at 10Hz,
    resolving conflicting sensor data with domain priorities:
    - Speed: CAN bus prioritized over GPS doppler speed due to transmission resolution.
    - Position / Heading: Sourced from GPS receiver.
    - Emergency State: CAN status prioritized, or manually overridden.
    - Acceleration: Calculated or obtained from CAN inertial sensors.
    """

    def __init__(
        self,
        gps_reader: GPSReader,
        can_reader: CANReader,
        obu_reader: OBUApiReader,
        station_id: int = config.STATION_ID,
    ):
        self.gps_reader = gps_reader
        self.can_reader = can_reader
        self.obu_reader = obu_reader
        self.station_id = station_id

        self._lock = threading.Lock()
        self._current_vehicle_data = VehicleData(station_id=station_id)
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._manual_emergency_override: Optional[bool] = None

    async def start(self) -> None:
        """Start the 10Hz aggregation task."""
        if self._running:
            return
        self._running = True
        logger.info("DataAggregator starting 10Hz sensor fusion loop")
        self._task = asyncio.create_task(self._aggregation_loop())

    async def stop(self) -> None:
        """Stop aggregation loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("DataAggregator stopped")

    def get_latest_vehicle_data(self) -> VehicleData:
        """Return atomic snapshot copy of the latest fused vehicle data."""
        with self._lock:
            # Return copy of dataclass
            data = self._current_vehicle_data
            return VehicleData(
                station_id=data.station_id,
                timestamp=data.timestamp,
                latitude=data.latitude,
                longitude=data.longitude,
                altitude=data.altitude,
                speed_kmh=data.speed_kmh,
                heading=data.heading,
                acceleration=data.acceleration,
                vehicle_width=data.vehicle_width,
                vehicle_length=data.vehicle_length,
                emergency_active=data.emergency_active,
                siren_active=data.siren_active,
                lights_active=data.lights_active,
                gps_fix_quality=data.gps_fix_quality,
                satellite_count=data.satellite_count,
                hdop=data.hdop,
                can_connected=data.can_connected,
                obu_link_quality=data.obu_link_quality,
            )

    def set_emergency_override(self, active: Optional[bool]) -> None:
        """Manually override emergency status or reset to sensor-driven."""
        self._manual_emergency_override = active
        logger.info(f"Emergency status override set to: {active}")

    async def _aggregation_loop(self) -> None:
        """10Hz fusion execution loop (every 100ms)."""
        interval = 1.0 / config.DATA_COLLECTION_RATE_HZ
        while self._running:
            start_time = asyncio.get_event_loop().time()
            try:
                self._fuse_data()
            except Exception as e:
                logger.error(f"Error fusing vehicle sensor data: {e}", exc_info=True)

            elapsed = asyncio.get_event_loop().time() - start_time
            sleep_duration = max(0.001, interval - elapsed)
            await asyncio.sleep(sleep_duration)

    def _fuse_data(self) -> None:
        """Perform sensor fusion according to priority and freshness rules."""
        now = datetime.now(timezone.utc)
        gps = self.gps_reader.get_latest_data()
        can = self.can_reader.get_latest_data()
        obu = self.obu_reader.get_latest_data()

        # GPS freshness
        gps_age = (now - gps.timestamp).total_seconds()
        gps_fresh = gps.is_valid and gps_age <= config.DATA_FRESHNESS_MAX_AGE_SEC

        # CAN freshness
        can_age = (now - can.timestamp).total_seconds()
        can_fresh = can.connected and can_age <= config.DATA_FRESHNESS_MAX_AGE_SEC

        # 1. Position & Heading: GPS is primary source
        lat = gps.latitude if gps_fresh else 0.0
        lon = gps.longitude if gps_fresh else 0.0
        alt = gps.altitude if gps_fresh else 0.0
        heading = gps.heading if gps_fresh else 0.0

        # 2. Speed: Prioritize CAN wheel speed over GPS doppler if CAN is connected and fresh
        if can_fresh and can.speed_kmh is not None:
            speed = can.speed_kmh
        elif gps_fresh:
            speed = gps.speed_kmh
        else:
            speed = 0.0

        # 3. Acceleration
        if can_fresh:
            accel = can.acceleration_ms2
        else:
            accel = 0.0

        # 4. Emergency status (Siren / Lights)
        siren = can.siren_active if can_fresh else False
        lights = can.lights_active if can_fresh else False

        if self._manual_emergency_override is not None:
            emergency = self._manual_emergency_override
            if emergency:
                siren = True
                lights = True
        else:
            emergency = siren or lights

        with self._lock:
            self._current_vehicle_data = VehicleData(
                station_id=self.station_id,
                timestamp=now,
                latitude=lat,
                longitude=lon,
                altitude=alt,
                speed_kmh=speed,
                heading=heading,
                acceleration=accel,
                vehicle_width=config.VEHICLE_WIDTH_CM,
                vehicle_length=config.VEHICLE_LENGTH_CM,
                emergency_active=emergency,
                siren_active=siren,
                lights_active=lights,
                gps_fix_quality=gps.fix_quality if gps_fresh else 0,
                satellite_count=gps.satellites if gps_fresh else 0,
                hdop=gps.hdop if gps_fresh else 99.9,
                can_connected=can.connected if can_fresh else False,
                obu_link_quality=obu.link_quality,
            )
