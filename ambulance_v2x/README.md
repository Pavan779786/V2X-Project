# Ambulance V2X On-Board Unit (OBU) Application

Production-quality, ETSI ITS / IEEE 802.11p / C-V2X compatible ambulance-side On-Board Unit (OBU) application in Python. Collects vehicle kinematics and sensor data, fuses them at 10 Hz, formats them into standardized ETSI ITS messages (CAM and DENM), encapsulates them with wire protocol framing and CRC32 integrity, and broadcasts them wirelessly to Roadside Units (RSUs) and nearby connected vehicles.

---

## 1. Architecture Overview

### End-to-End System Communication Flow
```
┌──────────────────────────────────────────────────────────────┐
│                    Ambulance Vehicle                         │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐  │
│  │ GNSS / GPS   │   │ CAN Bus OBD2 │   │ OBU Modem (REST/ │  │
│  │ Serial / NMEA│   │ Lights/Siren │   │ WebSocket API)   │  │
│  └──────┬───────┘   └──────┬───────┘   └────────┬─────────┘  │
│         │                  │                    │            │
│         ▼                  ▼                    ▼            │
│   [GPSReader]         [CANReader]         [OBUApiReader]     │
│         │                  │                    │            │
│         └──────────┐       │       ┌────────────┘            │
│                    ▼       ▼       ▼                         │
│               ┌────────────────────────┐                     │
│               │     DataAggregator     │ 10 Hz Fusion        │
│               │     (VehicleData)      │ Priority & Freshness│
│               └────────────┬───────────┘                     │
│                            ▼                                 │
│               ┌────────────────────────┐                     │
│               │     DataValidator      │ Limits & Plausibility│
│               └────────────┬───────────┘                     │
│                            ▼                                 │
│           ┌────────────────────────────────┐                 │
│           │   Message Processing Engine    │                 │
│           │   ├── CAMBuilder (EN 302637-2) │ 10 Hz CAM       │
│           │   ├── DENMBuilder (EN 302637-3)│ Emergency DENM │
│           │   └── MessageSerializer (JSON/ │                 │
│           │                          Bin)  │                 │
│           └────────────────┬───────────────┘                 │
│                            ▼                                 │
│               ┌────────────────────────┐                     │
│               │    ProtocolHandler     │ Magic + CRC32 Frame │
│               └────────────┬───────────┘                     │
│                            │                                 │
│              ┌─────────────┴─────────────┐                   │
│              │ UDP (10Hz CAM) │ TCP (DENM)│                   │
│              └──────┬────────────────────┘                   │
└─────────────────────┼────────────────────────────────────────┘
                      │ Wireless 5.9 GHz ITS-G5 / 802.11p / PC5
                      ▼
            ┌───────────────────┐
            │   Roadside Unit   │
            │       (RSU)       │
            └─────────┬─────────┘
                      │
         ┌────────────┴────────────┐
         ▼                         ▼
┌──────────────────┐      ┌──────────────────┐
│  Traffic Signal  │      │ Civilian Vehicle │
│   Green Light    │      │   OBU Display    │
│ Preemption (TSP) │      │  Emergency Alert │
└──────────────────┘      └──────────────────┘
```

---

## 2. Directory Structure

```
ambulance_v2x/
├── main.py                        # Application entry point and orchestrator
├── config.py                      # Global configuration and ETSI constants
├── requirements.txt               # Python package dependencies
├── README.md                      # Comprehensive documentation
├── data_collection/
│   ├── __init__.py
│   ├── gps_reader.py              # Serial NMEA / GPSD reader + route simulation fallback
│   ├── can_reader.py              # CAN OBD-II + emergency light signals + simulation fallback
│   ├── obu_api_reader.py          # OBU REST/WebSocket management reader + fallback
│   └── data_aggregator.py        # 10 Hz sensor fusion prioritizing CAN speed & GPS location
├── message_processing/
│   ├── __init__.py
│   ├── validator.py              # Physical bounds, plausibility, and freshness checks
│   ├── cam_builder.py            # ETSI EN 302 637-2 CAM message builder
│   ├── denm_builder.py           # ETSI EN 302 637-3 DENM emergency message builder
│   └── message_serializer.py    # UTF-8 JSON and compact struct binary serializers
├── communication/
│   ├── __init__.py
│   ├── rsu_client.py             # Asynchronous dual UDP (CAM) and TCP (DENM) socket client
│   ├── connection_manager.py    # Heartbeat monitoring and exponential backoff reconnection
│   └── protocol_handler.py      # Wire framing with CRC32 integrity verification
├── monitoring/
│   ├── __init__.py
│   ├── logger.py                 # Multi-target rotating file and colorized console logging
│   ├── health_monitor.py        # Diagnostic monitor for GPS, CAN, and RSU link states
│   └── dashboard.py             # Real-time interactive terminal dashboard (Rich)
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_validator.py         # Boundary and sanity unit tests
    ├── test_cam_builder.py       # Unit scaling and container completeness tests
    └── test_rsu_client.py        # Framing, CRC32, and transmission tests
```

---

## 3. Hardware Requirements

### Real Deployment Environment
1. **On-Board Unit (OBU)**:
   - Cohda Wireless MK5 / MK6, Commsignia Unex OBU, or Raspberry Pi 4 / CM4 equipped with a 5.9 GHz V2X transceiver (802.11p / ITS-G5 or C-V2X PC5).
2. **GNSS / GPS Receiver**:
   - High-precision GNSS module (u-blox ZED-F9P, NEO-M8N) supporting NMEA-0183 sentences (`$GPRMC`, `$GPGGA`) over UART/USB serial (`/dev/ttyUSB0` or `COM3`) at 9600-115200 baud.
3. **Vehicle CAN Bus Interface**:
   - PEAK-System PCAN-USB, Kvaser Leaf Light, or SocketCAN-compatible CAN interface (`can0` or `vcan0`).
   - Reads standard OBD-II PID `0x0D` (Speed) and `0x0C` (RPM), plus emergency lighting controller status on CAN ID `0x380`.

---

## 4. Installation and Setup

### Prerequisites
- Python 3.11 or newer (Python 3.13 fully supported)
- Windows, Linux (Ubuntu 22.04 / 24.04), or Raspberry Pi OS

### Setup Environment
```bash
# Clone or navigate to the project directory
cd c:\Users\hp\OneDrive\Desktop\V2X\ambulance_v2x

# (Optional) Create and activate virtual environment
python -m venv venv
# Windows:
.\venv\Scripts\activate
# Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## 5. Configuration Guide

All system settings are consolidated in `config.py` and can be overridden via environment variables or CLI flags:

| Environment Variable | Default | Description |
|----------------------|---------|-------------|
| `V2X_SIMULATION_MODE` | `true` | When true, uses realistic route and sensor simulation |
| `V2X_STATION_ID` | `1042` | Unique ETSI ITS Station ID |
| `V2X_CALLSIGN` | `MEDIC-01` | Ambulance callsign |
| `V2X_RSU_HOST` | `127.0.0.1` | Target RSU IP address |
| `V2X_RSU_UDP_PORT` | `5900` | UDP port for 10 Hz CAM broadcasts |
| `V2X_RSU_TCP_PORT` | `5901` | TCP port for DENM alerts and acknowledgements |
| `V2X_GPS_PORT` | `COM3` / `/dev/ttyUSB0` | Serial port for physical GPS |
| `V2X_CAN_INTERFACE` | `virtual` / `socketcan` | CAN bus interface driver |
| `V2X_CAN_CHANNEL` | `0` / `vcan0` | CAN bus channel identifier |
| `V2X_VEHICLE_WIDTH_CM` | `210` | Vehicle physical width in cm |
| `V2X_VEHICLE_LENGTH_CM`| `620` | Vehicle physical length in cm |

---

## 6. Running the Application

### 1. Simulation Mode (Default - Runs Without Hardware)
Runs with simulated GPS movement along an urban emergency route, realistic RPM/speed correlations, and simulated OBU RF links:
```bash
# Interactive mode with rich terminal dashboard
python main.py --simulate

# Run with emergency siren and lights actively broadcasting DENM
python main.py --simulate --emergency

# Headless mode (suitable for Docker, systemd, or background daemon)
python main.py --simulate --no-dashboard
```

### 2. Physical Hardware Mode
Connects to physical serial GPS (`COM3` or `/dev/ttyUSB0`) and vehicle CAN bus:
```bash
python main.py --hardware --rsu-host 192.168.1.100 --udp-port 5900 --tcp-port 5901
```

### CLI Command Options
```
usage: main.py [-h] [--simulate] [--hardware] [--rsu-host RSU_HOST]
               [--udp-port UDP_PORT] [--tcp-port TCP_PORT]
               [--station-id STATION_ID] [--emergency] [--no-dashboard]

Ambulance V2X OBU Application (ETSI ITS Compliant)

options:
  -h, --help            Show this help message and exit
  --simulate            Run with high-fidelity GPS and CAN sensor simulation
  --hardware            Attempt binding to real physical serial GPS and CAN interfaces
  --rsu-host RSU_HOST   Target Roadside Unit IP address (default: 127.0.0.1)
  --udp-port UDP_PORT   RSU UDP port for CAM broadcast (default: 5900)
  --tcp-port TCP_PORT   RSU TCP port for DENM alerts (default: 5901)
  --station-id ID       Unique ETSI ITS Station ID (default: 1042)
  --emergency           Start with emergency siren and beacon lights active
  --no-dashboard        Disable interactive terminal dashboard (plain logging)
```

---

## 7. V2X Message Specifications

### 1. ETSI EN 302 637-2 CAM (Cooperative Awareness Message)
Broadcast at 10 Hz over UDP.
- **Reference Position**:
  - `latitude`: scaled by $10^7$ (0.1 micro-degree)
  - `longitude`: scaled by $10^7$ (0.1 micro-degree)
  - `altitude`: scaled by $10^2$ (0.01 meter)
- **High Frequency Container**:
  - `heading`: 0.1 degree units ($0 \dots 3600$)
  - `speed`: 0.01 m/s units ($1 \text{ m/s} = 100$)
  - `driveDirection`: "forward"
  - `vehicleLength`: 0.1 meter units ($6.2\text{m} = 62$)
  - `vehicleWidth`: 0.1 meter units ($2.1\text{m} = 21$)
- **Special Vehicle Container**:
  - `stationType`: 10 (`specialVehicles`)
  - `lightBarSirenInUse`: siren and beacon light boolean flags
  - `causeCode`: 9 (`emergencyVehicleApproaching`), subcause: 1 (stationary) or 2 (mobile)
  - `emergencyPriority`: `requestForRightOfWay`, `requestForFreeCrossingAtTrafficLight`

### 2. ETSI EN 302 637-3 DENM (Decentralized Environmental Notification)
Dispatched when emergency mode is engaged or cleared.
- **Management Container**:
  - `actionID`: `{originatingStationID, sequenceNumber}`
  - `relevanceDistance`: `"lessThan1000m"`
  - `validityDuration`: 20 seconds
  - `relevanceTrafficDirection`: `"allTrafficDirections"`
- **Situation Container**:
  - `eventType`: `{causeCode: 9, subCauseCode: 2}`

### 3. Wire Protocol Framing
Every UDP datagram and TCP packet is framed with:
```
[MAGIC (4B)][VERSION (1B)][MSG_TYPE (1B)][SEQ_NUM (4B)][TIMESTAMP (8B)][LENGTH (4B)][PAYLOAD (NB)][CRC32 (4B)]
```
- `MAGIC`: ASCII `b"V2X1"`
- `MSG_TYPE`: `0x01` (CAM), `0x02` (DENM), `0x03` (HEARTBEAT), `0x04` (ACK)
- `CRC32`: IEEE 802.3 32-bit checksum covering the header and payload.

---

## 8. Diagnostic Logs

Structured log files are automatically maintained with rotation in `logs/`:
- `logs/app.log`: General operational events, state changes, and diagnostics.
- `logs/messages.log`: JSON-Lines audit trail recording every transmitted CAM and DENM payload.
- `logs/errors.log`: Filtered log recording warnings, validation anomalies, and network failures.

---

## 9. Running Tests

Execute the complete automated test suite with pytest:
```bash
python -m pytest ambulance_v2x/tests -v
```

All 21 unit tests cover:
- Data validator boundary enforcement (lat/lon limits, speed sanity, heading bounds, freshness).
- ETSI unit conversions and CAM/DENM dictionary compliance.
- Protocol wire packing, unpacking, CRC32 tampering detection, and mock socket sends.
