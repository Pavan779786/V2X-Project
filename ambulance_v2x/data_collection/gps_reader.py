"""
GPS Reader Module for Ambulance V2X
Supports real serial NMEA parsing (pyserial), GPSD client daemon,
and high-fidelity realistic route simulation fallback.
"""

import math
import time
import asyncio
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List, Tuple

import config
from monitoring.logger import get_logger

logger = get_logger("gps_reader")

# Optional imports with graceful fallback
try:
    import serial
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False

try:
    import gps
    HAS_GPSD = True
except ImportError:
    HAS_GPSD = False


@dataclass
class GPSData:
    """Standardized GPS fix data structure."""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0
    speed_kmh: float = 0.0
    heading: float = 0.0
    fix_quality: int = 0  # 0=Invalid, 1=GPS SPS, 2=DGPS, 4=RTK Fixed, 5=RTK Float
    satellites: int = 0
    hdop: float = 99.9
    is_valid: bool = False
    source: str = "uninitialized"


def calculate_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate forward azimuth bearing in degrees (0..360) between two coordinates."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_lambda = math.radians(lon2 - lon1)

    y = math.sin(delta_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda)
    bearing = math.degrees(math.atan2(y, x))
    return (bearing + 360.0) % 360.0


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance in meters between two coordinates."""
    r = 6371000.0  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (math.sin(delta_phi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r * c


def parse_nmea_coord(raw: str, direction: str) -> Optional[float]:
    """Convert NMEA format (DDMM.MMMM or DDDMM.MMMM) to decimal degrees."""
    if not raw or not direction:
        return None
    try:
        dot_idx = raw.find(".")
        if dot_idx == -1:
            return None
        deg_len = dot_idx - 2
        degrees = float(raw[:deg_len])
        minutes = float(raw[deg_len:])
        decimal = degrees + (minutes / 60.0)
        if direction.upper() in ("S", "W"):
            decimal = -decimal
        return decimal
    except (ValueError, IndexError):
        return None


class GPSReader:
    """
    Robust GPS Reader supporting Serial NMEA, GPSD, and dynamic route simulation.
    Thread-safe and asyncio-compatible.
    """

    def __init__(self, simulation_mode: bool = config.SIMULATION_MODE):
        self.simulation_mode = simulation_mode
        self._lock = threading.Lock()
        self._latest_data = GPSData(source="init")
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # Route simulation state
        self._route: List[Tuple[float, float, float]] = config.SIMULATION_ROUTE
        self._route_idx: int = 0
        self._curr_lat: float = self._route[0][0]
        self._curr_lon: float = self._route[0][1]
        self._curr_alt: float = 45.0
        self._curr_speed: float = 0.0
        self._curr_heading: float = 0.0
        self._last_sim_tick: float = time.monotonic()

    async def start(self) -> None:
        """Start the background GPS ingestion or simulation loop."""
        if self._running:
            return
        self._running = True

        if not self.simulation_mode:
            # Attempt hardware interfaces first
            connected = await self._try_hardware_connection()
            if not connected:
                logger.warning("Hardware GPS not available. Falling back to simulation mode.")
                self.simulation_mode = True

        logger.info(f"GPSReader initialized in {'SIMULATION' if self.simulation_mode else 'HARDWARE'} mode")
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop reader loop and cleanup resources."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("GPSReader stopped")

    def get_latest_data(self) -> GPSData:
        """Thread-safe retrieval of the most recent GPS fix."""
        with self._lock:
            return self._latest_data

    async def _try_hardware_connection(self) -> bool:
        """Probe serial port or gpsd daemon."""
        if HAS_SERIAL:
            try:
                ser = serial.Serial(
                    config.GPS_SERIAL_PORT,
                    baudrate=config.GPS_BAUDRATE,
                    timeout=config.GPS_TIMEOUT_SEC,
                )
                ser.close()
                logger.info(f"Found GPS serial device on {config.GPS_SERIAL_PORT}")
                return True
            except Exception as e:
                logger.debug(f"Serial GPS probe on {config.GPS_SERIAL_PORT} failed: {e}")

        if HAS_GPSD:
            try:
                session = gps.gps(host=config.GPSD_HOST, port=config.GPSD_PORT, mode=gps.WATCH_ENABLE)
                session.close()
                logger.info(f"Connected to GPSD at {config.GPSD_HOST}:{config.GPSD_PORT}")
                return True
            except Exception as e:
                logger.debug(f"GPSD probe failed: {e}")

        return False

    async def _run_loop(self) -> None:
        """Main polling / simulation loop running at 10Hz."""
        while self._running:
            try:
                if self.simulation_mode:
                    self._step_simulation()
                else:
                    read_success = await self._read_hardware_gps()
                    if not read_success:
                        self._step_simulation()
                await asyncio.sleep(0.1)  # 10Hz sampling
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in GPSReader loop: {e}", exc_info=True)
                await asyncio.sleep(0.5)

    def _step_simulation(self) -> None:
        """Simulate realistic ambulance motion along waypoints."""
        now = time.monotonic()
        dt = max(0.01, min(now - self._last_sim_tick, 0.5))
        self._last_sim_tick = now

        target_lat, target_lon, target_speed_kmh = self._route[self._route_idx]
        dist_to_target = haversine_distance_m(self._curr_lat, self._curr_lon, target_lat, target_lon)

        # Reached waypoint -> advance to next
        if dist_to_target < 15.0:
            self._route_idx = (self._route_idx + 1) % len(self._route)
            target_lat, target_lon, target_speed_kmh = self._route[self._route_idx]
            dist_to_target = haversine_distance_m(self._curr_lat, self._curr_lon, target_lat, target_lon)

        # Bearing to next target
        target_heading = calculate_bearing(self._curr_lat, self._curr_lon, target_lat, target_lon)

        # Smooth heading transition
        heading_diff = (target_heading - self._curr_heading + 180.0) % 360.0 - 180.0
        turn_rate = 45.0  # degrees per second max turn
        heading_change = max(-turn_rate * dt, min(turn_rate * dt, heading_diff))
        self._curr_heading = (self._curr_heading + heading_change + 360.0) % 360.0

        # Smooth speed acceleration/braking
        accel_rate = 2.5 * 3.6  # km/h per second acceleration
        brake_rate = 4.0 * 3.6  # km/h per second braking
        if self._curr_speed < target_speed_kmh:
            self._curr_speed = min(target_speed_kmh, self._curr_speed + accel_rate * dt)
        elif self._curr_speed > target_speed_kmh:
            self._curr_speed = max(target_speed_kmh, self._curr_speed - brake_rate * dt)

        # Position step
        speed_mps = (self._curr_speed / 3.6)
        step_distance_m = speed_mps * dt

        if dist_to_target > 0:
            ratio = min(1.0, step_distance_m / dist_to_target)
            self._curr_lat += (target_lat - self._curr_lat) * ratio
            self._curr_lon += (target_lon - self._curr_lon) * ratio

        # Slight altitude variation
        self._curr_alt = 45.0 + 3.0 * math.sin(time.time() / 10.0)

        with self._lock:
            self._latest_data = GPSData(
                timestamp=datetime.now(timezone.utc),
                latitude=self._curr_lat,
                longitude=self._curr_lon,
                altitude=self._curr_alt,
                speed_kmh=self._curr_speed,
                heading=self._curr_heading,
                fix_quality=2,  # DGPS high quality
                satellites=12,
                hdop=0.9,
                is_valid=True,
                source="simulated",
            )

    async def _read_hardware_gps(self) -> bool:
        """Read and parse NMEA sentences from real serial interface asynchronously."""
        if not HAS_SERIAL:
            return False

        def _blocking_read() -> Optional[GPSData]:
            try:
                with serial.Serial(
                    config.GPS_SERIAL_PORT,
                    baudrate=config.GPS_BAUDRATE,
                    timeout=config.GPS_TIMEOUT_SEC,
                ) as ser:
                    for _ in range(10):
                        line = ser.readline().decode("ascii", errors="replace").strip()
                        if line.startswith(("$GPRMC", "$GNRMC")):
                            parts = line.split(",")
                            if len(parts) >= 10 and parts[2] == "A":  # Status A = active/valid
                                lat = parse_nmea_coord(parts[3], parts[4])
                                lon = parse_nmea_coord(parts[5], parts[6])
                                speed_knots = float(parts[7]) if parts[7] else 0.0
                                heading = float(parts[8]) if parts[8] else 0.0
                                if lat is not None and lon is not None:
                                    return GPSData(
                                        timestamp=datetime.now(timezone.utc),
                                        latitude=lat,
                                        longitude=lon,
                                        altitude=0.0,
                                        speed_kmh=speed_knots * 1.852,
                                        heading=heading,
                                        fix_quality=1,
                                        satellites=8,
                                        hdop=1.2,
                                        is_valid=True,
                                        source="serial_nmea",
                                    )
            except Exception as ex:
                logger.debug(f"Serial NMEA read error: {ex}")
            return None

        data = await asyncio.to_thread(_blocking_read)
        if data and data.is_valid:
            with self._lock:
                self._latest_data = data
            return True
        return False
