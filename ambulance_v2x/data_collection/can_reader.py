"""
CAN Bus Reader Module for Ambulance V2X
Supports real CAN hardware via python-can (SocketCAN, PCAN, Vector, Virtual)
and dynamic realistic signal simulation with OBD-II and proprietary ambulance DBC signals.
"""

import time
import asyncio
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any

import config
from monitoring.logger import get_logger

logger = get_logger("can_reader")

try:
    import can
    HAS_PYTHON_CAN = True
except ImportError:
    HAS_PYTHON_CAN = False

# Standard OBD-II & Proprietary CAN IDs
OBD_REQUEST_ID = 0x7DF
OBD_RESPONSE_ID = 0x7E8
AMBULANCE_STATUS_CAN_ID = 0x380  # Custom Ambulance Telemetry Frame


@dataclass
class CANData:
    """Standardized vehicle internal bus telemetry."""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    speed_kmh: float = 0.0
    rpm: float = 0.0
    engine_load_pct: float = 0.0
    siren_active: bool = False
    lights_active: bool = False
    brake_active: bool = False
    steering_angle_deg: float = 0.0
    acceleration_ms2: float = 0.0
    connected: bool = False
    source: str = "uninitialized"


class CANReader:
    """
    CAN bus interface reader supporting real hardware buses and dynamic simulation.
    Decodes standard OBD-II frames and proprietary emergency lighting signals.
    """

    def __init__(self, simulation_mode: bool = config.SIMULATION_MODE):
        self.simulation_mode = simulation_mode
        self._lock = threading.Lock()
        self._latest_data = CANData(source="init")
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._bus: Optional[Any] = None

        # Simulated state
        self._sim_speed: float = 0.0
        self._sim_rpm: float = 850.0
        self._sim_siren: bool = True
        self._sim_lights: bool = True
        self._prev_speed_mps: float = 0.0
        self._last_tick: float = time.monotonic()

    async def start(self) -> None:
        """Start the CAN bus reader task."""
        if self._running:
            return
        self._running = True

        if not self.simulation_mode:
            connected = self._init_hardware_can()
            if not connected:
                logger.warning("CAN hardware interface unavailable. Switching to CAN simulation.")
                self.simulation_mode = True

        logger.info(f"CANReader started in {'SIMULATION' if self.simulation_mode else 'HARDWARE'} mode")
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop reader loop and close hardware bus."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        if self._bus is not None:
            try:
                self._bus.shutdown()
            except Exception as e:
                logger.debug(f"CAN bus shutdown error: {e}")
            self._bus = None

        logger.info("CANReader stopped")

    def get_latest_data(self) -> CANData:
        """Thread-safe retrieval of latest CAN telemetry."""
        with self._lock:
            return self._latest_data

    def set_emergency_mode(self, siren: bool, lights: bool) -> None:
        """Manually trigger or toggle simulated emergency siren and beacon lights."""
        with self._lock:
            self._sim_siren = siren
            self._sim_lights = lights
            logger.info(f"Ambulance emergency mode set: siren={siren}, lights={lights}")

    def _init_hardware_can(self) -> bool:
        """Initialize python-can Bus."""
        if not HAS_PYTHON_CAN:
            return False
        try:
            self._bus = can.Bus(
                interface=config.CAN_INTERFACE,
                channel=config.CAN_CHANNEL,
                bitrate=config.CAN_BITRATE,
                receive_own_messages=False,
            )
            logger.info(f"CAN bus connected on {config.CAN_INTERFACE}::{config.CAN_CHANNEL}")
            return True
        except Exception as e:
            logger.debug(f"Failed to connect to CAN interface {config.CAN_INTERFACE}: {e}")
            self._bus = None
            return False

    async def _run_loop(self) -> None:
        """Main CAN sampling loop running at 10Hz."""
        while self._running:
            try:
                if self.simulation_mode:
                    self._step_simulation()
                else:
                    read_success = await self._read_hardware_frames()
                    if not read_success:
                        self._step_simulation()
                await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in CANReader loop: {e}", exc_info=True)
                await asyncio.sleep(0.5)

    def _step_simulation(self) -> None:
        """Generate realistic correlated CAN telemetry (RPM proportional to speed, acceleration)."""
        now = time.monotonic()
        dt = max(0.01, min(now - self._last_tick, 0.5))
        self._last_tick = now

        # Oscillate speed between 30 and 80 km/h with gentle acceleration profile
        t = now / 4.0
        target_speed = 55.0 + 25.0 * (0.5 * (1.0 + (t % 2.0 - 1.0) if t % 2.0 < 1.0 else 1.0 - (t % 1.0)))
        speed_delta = (target_speed - self._sim_speed) * 0.2
        self._sim_speed = max(0.0, self._sim_speed + speed_delta)

        # Correlated RPM: Idle ~850, redline 4500
        gear_ratio = 45.0
        self._sim_rpm = 850.0 + (self._sim_speed * gear_ratio)

        # Acceleration in m/s^2
        curr_speed_mps = self._sim_speed / 3.6
        accel = (curr_speed_mps - self._prev_speed_mps) / dt
        self._prev_speed_mps = curr_speed_mps

        engine_load = min(100.0, max(15.0, 20.0 + abs(accel) * 18.0 + (self._sim_speed / 2.0)))
        brake_active = accel < -1.5

        with self._lock:
            self._latest_data = CANData(
                timestamp=datetime.now(timezone.utc),
                speed_kmh=round(self._sim_speed, 2),
                rpm=round(self._sim_rpm, 1),
                engine_load_pct=round(engine_load, 1),
                siren_active=self._sim_siren,
                lights_active=self._sim_lights,
                brake_active=brake_active,
                steering_angle_deg=0.0,
                acceleration_ms2=round(accel, 2),
                connected=True,
                source="simulated",
            )

    async def _read_hardware_frames(self) -> bool:
        """Poll CAN bus for OBD-II and proprietary ambulance messages."""
        if not self._bus:
            return False

        def _recv_blocking() -> Optional[CANData]:
            try:
                # Non-blocking fetch up to 5 messages
                updated_speed = None
                updated_rpm = None
                updated_lights = None
                updated_siren = None

                for _ in range(5):
                    msg = self._bus.recv(timeout=0.02)
                    if msg is None:
                        break

                    # Standard OBD-II Response
                    if msg.arbitration_id == OBD_RESPONSE_ID:
                        # Byte 1: Mode (0x41 = Show Current Data), Byte 2: PID
                        if len(msg.data) >= 4 and msg.data[1] == 0x41:
                            pid = msg.data[2]
                            if pid == 0x0D:  # Speed PID
                                updated_speed = float(msg.data[3])
                            elif pid == 0x0C and len(msg.data) >= 5:  # Engine RPM
                                updated_rpm = float((msg.data[3] * 256 + msg.data[4]) / 4.0)

                    # Proprietary Ambulance Control Frame
                    elif msg.arbitration_id == AMBULANCE_STATUS_CAN_ID:
                        # Byte 0: Bit 0 = lights, Bit 1 = siren
                        if len(msg.data) >= 1:
                            status_byte = msg.data[0]
                            updated_lights = bool(status_byte & 0x01)
                            updated_siren = bool(status_byte & 0x02)

                if updated_speed is not None or updated_lights is not None:
                    return CANData(
                        timestamp=datetime.now(timezone.utc),
                        speed_kmh=updated_speed if updated_speed is not None else self._latest_data.speed_kmh,
                        rpm=updated_rpm if updated_rpm is not None else self._latest_data.rpm,
                        engine_load_pct=self._latest_data.engine_load_pct,
                        siren_active=updated_siren if updated_siren is not None else self._latest_data.siren_active,
                        lights_active=updated_lights if updated_lights is not None else self._latest_data.lights_active,
                        brake_active=False,
                        steering_angle_deg=0.0,
                        acceleration_ms2=0.0,
                        connected=True,
                        source="socketcan",
                    )
            except Exception as e:
                logger.debug(f"CAN frame read error: {e}")
            return None

        result = await asyncio.to_thread(_recv_blocking)
        if result:
            with self._lock:
                self._latest_data = result
            return True
        return False
