"""
Ambulance V2X Configuration Module
Contains all configuration constants, ETSI ITS protocol specifications,
wire framing constants, and hardware interface definitions.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Tuple

# Base paths
BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

APP_LOG_PATH = LOG_DIR / "app.log"
MESSAGES_LOG_PATH = LOG_DIR / "messages.log"
ERRORS_LOG_PATH = LOG_DIR / "errors.log"

# Operating Mode
SIMULATION_MODE: bool = os.getenv("V2X_SIMULATION_MODE", "true").lower() in ("true", "1", "yes")

# Station & Vehicle Identification
STATION_ID: int = int(os.getenv("V2X_STATION_ID", "1042"))  # Unique ITS Station ID
AMBULANCE_CALLSIGN: str = os.getenv("V2X_CALLSIGN", "MEDIC-01")
STATION_TYPE_SPECIAL_VEHICLE: int = 10  # ETSI TS 102 894-2 StationType (10 = specialVehicles)

# Vehicle Physical Dimensions (ETSI ITS: width in cm, length in cm)
VEHICLE_WIDTH_CM: int = int(os.getenv("V2X_VEHICLE_WIDTH_CM", "210"))   # 2.1 meters
VEHICLE_LENGTH_CM: int = int(os.getenv("V2X_VEHICLE_LENGTH_CM", "620")) # 6.2 meters

# Roadside Unit (RSU) Networking
RSU_HOST: str = os.getenv("V2X_RSU_HOST", "127.0.0.1")
RSU_UDP_PORT: int = int(os.getenv("V2X_RSU_UDP_PORT", "5900"))  # CAM broadcast
RSU_TCP_PORT: int = int(os.getenv("V2X_RSU_TCP_PORT", "5901"))  # DENM & reliable alerts
SOCKET_TIMEOUT_SEC: float = 2.0
HEARTBEAT_INTERVAL_SEC: float = 1.0
TCP_RECONNECT_INITIAL_DELAY: float = 1.0
TCP_RECONNECT_MAX_DELAY: float = 30.0
TCP_RECONNECT_BACKOFF_FACTOR: float = 2.0

# Transmission Frequencies
CAM_INTERVAL_SEC: float = 0.1   # 10 Hz (100 ms) ETSI standard rate
DENM_REPEAT_INTERVAL_SEC: float = 1.0 # 1 Hz repeat when active
DATA_COLLECTION_RATE_HZ: float = 10.0 # 10 Hz aggregation loop
DASHBOARD_REFRESH_SEC: float = 0.5   # 2 Hz terminal UI update

# Data Collection Interfaces
GPS_SERIAL_PORT: str = os.getenv("V2X_GPS_PORT", "COM3" if os.name == "nt" else "/dev/ttyUSB0")
GPS_BAUDRATE: int = int(os.getenv("V2X_GPS_BAUDRATE", "9600"))
GPS_TIMEOUT_SEC: float = 1.0
GPSD_HOST: str = os.getenv("V2X_GPSD_HOST", "127.0.0.1")
GPSD_PORT: int = int(os.getenv("V2X_GPSD_PORT", "2947"))

CAN_INTERFACE: str = os.getenv("V2X_CAN_INTERFACE", "virtual")  # virtual, socketcan, pcan, etc.
CAN_CHANNEL: str = os.getenv("V2X_CAN_CHANNEL", "0" if os.name == "nt" else "vcan0")
CAN_BITRATE: int = int(os.getenv("V2X_CAN_BITRATE", "500000"))

OBU_API_BASE_URL: str = os.getenv("V2X_OBU_API_URL", "http://127.0.0.1:8080/api/v1")
OBU_WS_URL: str = os.getenv("V2X_OBU_WS_URL", "ws://127.0.0.1:8080/ws/telemetry")
OBU_REQUEST_TIMEOUT_SEC: float = 2.0

# ETSI ITS Protocol Constants (EN 302 637-2 / EN 302 637-3)
ETSI_PROTOCOL_VERSION: int = 2
MESSAGE_ID_DENM: int = 1
MESSAGE_ID_CAM: int = 2

# ETSI Unit Scale Factors
# Latitude/Longitude: 1e7 multiplier (0.1 micro-degree)
# ETSI standard unavailable value: 900000001 (Lat), 1800000001 (Lon)
LAT_LON_SCALE_FACTOR: float = 1e7
ETSI_LATITUDE_UNAVAILABLE: int = 900000001
ETSI_LONGITUDE_UNAVAILABLE: int = 1800000001

# Speed: 0.01 m/s (e.g., 1 m/s -> 100, 50 km/h = 13.88 m/s -> 1388)
# ETSI standard unavailable value: 16383
SPEED_SCALE_FACTOR_MPS_TO_ETSI: float = 100.0
KMH_TO_MPS: float = 1.0 / 3.6
ETSI_SPEED_UNAVAILABLE: int = 16383

# Heading: 0.1 degree units (0 to 3600, North=0, East=900, South=1800, West=2700)
# ETSI standard unavailable value: 3601
HEADING_SCALE_FACTOR: float = 10.0
ETSI_HEADING_UNAVAILABLE: int = 3601

# Longitudinal Acceleration: 0.1 m/s^2 (-160 to 160)
# ETSI standard unavailable value: 161
ACCEL_SCALE_FACTOR: float = 10.0
ETSI_ACCEL_UNAVAILABLE: int = 161

# Altitude: 0.01 m units (-100000 to 800000)
# ETSI standard unavailable value: 800001
ALTITUDE_SCALE_FACTOR: float = 100.0
ETSI_ALTITUDE_UNAVAILABLE: int = 800001

# Special Vehicle Container Constants
CAUSE_CODE_EMERGENCY_VEHICLE: int = 9  # ETSI EN 302 637-3
SUB_CAUSE_CODE_STATIONARY: int = 1
SUB_CAUSE_CODE_APPROACHING_MOBILE: int = 2

EMERGENCY_PRIORITY_RIGHT_OF_WAY: str = "requestForRightOfWay"
EMERGENCY_PRIORITY_TRAFFIC_LIGHT: str = "requestForFreeCrossingAtTrafficLight"

# Wire Protocol Framing Constants
# [MAGIC(4B)][VERSION(1B)][MSG_TYPE(1B)][SEQ_NUM(4B)][TIMESTAMP(8B)][LENGTH(4B)][PAYLOAD][CRC32(4B)]
WIRE_MAGIC: bytes = b"V2X1"
WIRE_VERSION: int = 1

MSG_TYPE_CAM: int = 0x01
MSG_TYPE_DENM: int = 0x02
MSG_TYPE_HEARTBEAT: int = 0x03
MSG_TYPE_ACK: int = 0x04

HEADER_STRUCT_FORMAT: str = "!4sBBIIQI" # magic (4s), version (B), msg_type (B), seq_num (I), reserved (I), timestamp_ms (Q), length (I)
HEADER_SIZE_BYTES: int = 4 + 1 + 1 + 4 + 4 + 8 + 4 # 26 bytes
CRC_SIZE_BYTES: int = 4

# Validation Thresholds
MIN_LATITUDE: float = -90.0
MAX_LATITUDE: float = 90.0
MIN_LONGITUDE: float = -180.0
MAX_LONGITUDE: float = 180.0
MIN_SPEED_KMH: float = 0.0
MAX_SPEED_KMH: float = 200.0
MIN_HEADING_DEG: float = 0.0
MAX_HEADING_DEG: float = 360.0
MAX_ACCELERATION_MS2: float = 10.0
MIN_ACCELERATION_MS2: float = -12.0
DATA_FRESHNESS_MAX_AGE_SEC: float = 2.0
MIN_ACCEPTABLE_HDOP: float = 5.0
MIN_ACCEPTABLE_SATELLITES: int = 4

# Simulation Route Waypoints (Realistic Emergency Route, e.g. Hospital to Scene)
# Coordinates representing an urban run in latitude, longitude, target speed km/h
SIMULATION_ROUTE: List[Tuple[float, float, float]] = [
    (52.520008, 13.404954, 45.0), # Station start
    (52.520850, 13.406200, 60.0), # Main avenue acceleration
    (52.521800, 13.408100, 75.0), # Straight stretch with siren
    (52.522900, 13.410500, 50.0), # Approaching intersection
    (52.524200, 13.411800, 70.0), # Intersection cleared
    (52.526000, 13.414000, 80.0), # Arterial corridor
    (52.527500, 13.412000, 40.0), # Left turn corner
    (52.528800, 13.409000, 65.0), # Hospital boulevard
    (52.530100, 13.406000, 30.0), # Hospital ramp
    (52.530800, 13.405000, 10.0), # Emergency bay arrival
    (52.531000, 13.404500, 0.0),  # Stopped at ER
]

# Health Monitor Settings
HEALTH_CHECK_INTERVAL_SEC: float = 1.0
GPS_HEALTH_TIMEOUT_SEC: float = 3.0
CAN_HEALTH_TIMEOUT_SEC: float = 3.0
RSU_HEARTBEAT_TIMEOUT_SEC: float = 4.0
