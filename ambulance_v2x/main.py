"""
Ambulance V2X On-Board Unit (OBU) Application Orchestrator
Main entry point coordinating sensor ingestion, sensor fusion,
ETSI ITS CAM/DENM generation, protocol framing, and RSU wireless transmission.
"""

import sys
from pathlib import Path

# Ensure ambulance_v2x root directory is in sys.path
_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import signal
import asyncio
import argparse
from typing import Optional

import config
from monitoring.logger import setup_logging, get_logger, log_v2x_message
from monitoring.health_monitor import HealthMonitor
from monitoring.dashboard import TerminalDashboard
from data_collection.gps_reader import GPSReader
from data_collection.can_reader import CANReader
from data_collection.obu_api_reader import OBUApiReader
from data_collection.data_aggregator import DataAggregator, VehicleData
from message_processing.validator import DataValidator
from message_processing.cam_builder import CAMBuilder
from message_processing.denm_builder import DENMBuilder
from message_processing.message_serializer import MessageSerializer
from communication.protocol_handler import ProtocolHandler
from communication.rsu_client import RSUClient
from communication.connection_manager import ConnectionManager

logger = get_logger("orchestrator")


class AmbulanceV2XApp:
    """
    Coordinates lifecycle of all Ambulance V2X subsystems:
    - Ingestion: GPS, CAN, OBU REST
    - Processing: 10Hz Sensor Fusion, Sanitization, CAM & DENM Builders
    - Transmission: Dual-channel UDP/TCP RSU transmission
    - Diagnostics: Health supervision and Terminal Dashboard
    """

    def __init__(
        self,
        simulation_mode: bool = config.SIMULATION_MODE,
        rsu_host: str = config.RSU_HOST,
        udp_port: int = config.RSU_UDP_PORT,
        tcp_port: int = config.RSU_TCP_PORT,
        station_id: int = config.STATION_ID,
        enable_dashboard: bool = True,
        initial_emergency: bool = False,
    ):
        self.simulation_mode = simulation_mode
        self.station_id = station_id
        self.enable_dashboard = enable_dashboard
        self.initial_emergency = initial_emergency

        # Subsystems
        self.gps_reader = GPSReader(simulation_mode=simulation_mode)
        self.can_reader = CANReader(simulation_mode=simulation_mode)
        self.obu_reader = OBUApiReader(simulation_mode=simulation_mode)
        self.aggregator = DataAggregator(
            gps_reader=self.gps_reader,
            can_reader=self.can_reader,
            obu_reader=self.obu_reader,
            station_id=station_id,
        )

        self.validator = DataValidator()
        self.cam_builder = CAMBuilder(station_id=station_id)
        self.denm_builder = DENMBuilder(station_id=station_id)
        self.serializer = MessageSerializer()
        self.protocol_handler = ProtocolHandler()

        self.rsu_client = RSUClient(
            rsu_host=rsu_host,
            udp_port=udp_port,
            tcp_port=tcp_port,
        )
        self.connection_manager = ConnectionManager(
            rsu_client=self.rsu_client,
            protocol_handler=self.protocol_handler,
            station_id=station_id,
        )
        self.health_monitor = HealthMonitor(
            aggregator=self.aggregator,
            connection_manager=self.connection_manager,
        )
        self.dashboard = TerminalDashboard(
            aggregator=self.aggregator,
            rsu_client=self.rsu_client,
            health_monitor=self.health_monitor,
        )

        self._running = False
        self._shutdown_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._prev_emergency_state = False

    async def start(self) -> None:
        """Start all subsystems and scheduling loops."""
        logger.info(f"Starting Ambulance V2X OBU Application (Station ID: {self.station_id})")
        logger.info(f"Target RSU: {self.rsu_client.rsu_host} (UDP: {self.rsu_client.udp_port}, TCP: {self.rsu_client.tcp_port})")
        logger.info(f"Operating Mode: {'SIMULATION' if self.simulation_mode else 'HARDWARE'}")

        self._running = True

        # 1. Start ingestion readers
        await self.gps_reader.start()
        await self.can_reader.start()
        await self.obu_reader.start()
        await self.aggregator.start()

        # Set initial emergency mode if configured
        if self.initial_emergency:
            self.aggregator.set_emergency_override(True)
            self.can_reader.set_emergency_mode(siren=True, lights=True)

        # 2. Start communication layer
        await self.rsu_client.start()
        await self.connection_manager.start()

        # 3. Start diagnostic systems
        await self.health_monitor.start()
        if self.enable_dashboard and sys.stdout.isatty():
            await self.dashboard.start()

        # 4. Launch primary V2X transmission loops
        self._tasks.append(asyncio.create_task(self._cam_broadcast_loop(), name="CAM_Broadcast"))
        self._tasks.append(asyncio.create_task(self._denm_broadcast_loop(), name="DENM_Broadcast"))

        logger.info("All Ambulance V2X subsystems initialized and running")
        self.dashboard.log_event("Subsystems operational. 10Hz CAM transmission active.")

    async def stop(self) -> None:
        """Gracefully stop all tasks and release resources."""
        if not self._running:
            return
        logger.info("Initiating graceful shutdown of Ambulance V2X...")
        self._running = False
        self._shutdown_event.set()

        # Cancel running transmission tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Stop monitoring & dashboard
        await self.dashboard.stop()
        await self.health_monitor.stop()

        # Stop communication
        await self.connection_manager.stop()
        await self.rsu_client.stop()

        # Stop data collection
        await self.aggregator.stop()
        await self.obu_reader.stop()
        await self.can_reader.stop()
        await self.gps_reader.stop()

        logger.info("Ambulance V2X application shutdown completed")

    async def wait_until_stopped(self) -> None:
        """Block until shutdown event is signaled."""
        await self._shutdown_event.wait()

    async def _cam_broadcast_loop(self) -> None:
        """
        10Hz ETSI ITS Cooperative Awareness Message (CAM) Broadcast Loop.
        Executed every 100 milliseconds per ETSI EN 302 637-2.
        """
        interval = config.CAM_INTERVAL_SEC
        while self._running:
            start_time = asyncio.get_event_loop().time()
            try:
                vehicle_data = self.aggregator.get_latest_vehicle_data()
                validation = self.validator.validate(vehicle_data)

                if validation.is_valid:
                    # Construct ETSI CAM message dictionary
                    cam_dict = self.cam_builder.build(vehicle_data)
                    payload_bytes = self.serializer.to_json_bytes(cam_dict)

                    # Wrap with wire protocol frame
                    seq = self.protocol_handler.next_sequence()
                    framed_packet = self.protocol_handler.pack_frame(
                        msg_type=config.MSG_TYPE_CAM,
                        payload=payload_bytes,
                        seq_num=seq,
                    )

                    # Transmit via UDP to RSU
                    sent = await self.rsu_client.send_cam(framed_packet)

                    # Log to structured message audit log
                    log_v2x_message(
                        msg_type="CAM",
                        station_id=vehicle_data.station_id,
                        seq_num=seq,
                        payload=cam_dict,
                        transport="UDP",
                        status="SENT" if sent else "FAILED",
                    )
                else:
                    self.dashboard.log_event(f"CAM Skipped: {validation.summary()}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in CAM broadcast cycle: {e}", exc_info=True)

            elapsed = asyncio.get_event_loop().time() - start_time
            sleep_time = max(0.001, interval - elapsed)
            await asyncio.sleep(sleep_time)

    async def _denm_broadcast_loop(self) -> None:
        """
        DENM Transmission Loop.
        Broadcasts Decentralized Environmental Notification Messages when emergency mode is active.
        Sends cancellation frame when emergency is cleared.
        """
        while self._running:
            try:
                vehicle_data = self.aggregator.get_latest_vehicle_data()
                current_emergency = vehicle_data.emergency_active

                # State Transition: Emergency newly activated
                if current_emergency and not self._prev_emergency_state:
                    logger.warning("EMERGENCY ACTIVATED: Triggering priority DENM dissemination!")
                    self.dashboard.log_event("EMERGENCY ACTIVE: Siren/Lights ON -> Emitting DENM")

                # State Transition: Emergency deactivated
                elif not current_emergency and self._prev_emergency_state:
                    logger.info("EMERGENCY CLEARED: Sending DENM cancellation")
                    self.dashboard.log_event("EMERGENCY CLEARED -> Sending Cancellation")
                    await self._send_denm_packet(vehicle_data, is_cancellation=True)

                self._prev_emergency_state = current_emergency

                # Periodic DENM repeat while emergency remains active (1Hz)
                if current_emergency:
                    await self._send_denm_packet(vehicle_data, is_cancellation=False)

                await asyncio.sleep(config.DENM_REPEAT_INTERVAL_SEC)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in DENM broadcast loop: {e}", exc_info=True)
                await asyncio.sleep(1.0)

    async def _send_denm_packet(self, data: VehicleData, is_cancellation: bool = False) -> None:
        """Build, serialize, and deliver a DENM notification."""
        denm_dict = self.denm_builder.build(data, is_cancellation=is_cancellation)
        payload_bytes = self.serializer.to_json_bytes(denm_dict)

        seq = self.protocol_handler.next_sequence()
        framed_packet = self.protocol_handler.pack_frame(
            msg_type=config.MSG_TYPE_DENM,
            payload=payload_bytes,
            seq_num=seq,
        )

        # Transmit over reliable TCP (with automatic UDP fallback)
        sent = await self.rsu_client.send_denm(framed_packet)

        log_v2x_message(
            msg_type="DENM_CANCEL" if is_cancellation else "DENM",
            station_id=data.station_id,
            seq_num=seq,
            payload=denm_dict,
            transport="TCP",
            status="SENT" if sent else "FAILED",
        )


def parse_arguments() -> argparse.Namespace:
    """Parse command-line configurations."""
    parser = argparse.ArgumentParser(
        description="Ambulance V2X OBU Application (ETSI ITS Compliant)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--simulate",
        dest="simulation_mode",
        action="store_true",
        default=config.SIMULATION_MODE,
        help="Run with high-fidelity GPS and CAN sensor simulation",
    )
    parser.add_argument(
        "--hardware",
        dest="simulation_mode",
        action="store_false",
        help="Attempt binding to real physical serial GPS and CAN interfaces",
    )
    parser.add_argument(
        "--rsu-host",
        type=str,
        default=config.RSU_HOST,
        help="Target Roadside Unit IP address",
    )
    parser.add_argument(
        "--udp-port",
        type=int,
        default=config.RSU_UDP_PORT,
        help="RSU UDP port for CAM broadcast",
    )
    parser.add_argument(
        "--tcp-port",
        type=int,
        default=config.RSU_TCP_PORT,
        help="RSU TCP port for DENM alerts",
    )
    parser.add_argument(
        "--station-id",
        type=int,
        default=config.STATION_ID,
        help="Unique ETSI ITS Station ID for this ambulance",
    )
    parser.add_argument(
        "--emergency",
        action="store_true",
        default=False,
        help="Start ambulance with emergency siren and beacon lights enabled",
    )
    parser.add_argument(
        "--no-dashboard",
        dest="enable_dashboard",
        action="store_false",
        default=True,
        help="Disable interactive terminal dashboard (plain logging output)",
    )
    return parser.parse_args()


async def main() -> None:
    """Application main entry point."""
    args = parse_arguments()

    # Initialize structured multi-channel logging
    # If dashboard is active and running in a TTY, suppress console log clutter
    setup_logging(enable_console=not (args.enable_dashboard and sys.stdout.isatty()))

    app = AmbulanceV2XApp(
        simulation_mode=args.simulation_mode,
        rsu_host=args.rsu_host,
        udp_port=args.udp_port,
        tcp_port=args.tcp_port,
        station_id=args.station_id,
        enable_dashboard=args.enable_dashboard,
        initial_emergency=args.emergency,
    )

    loop = asyncio.get_running_loop()

    def handle_signal():
        logger.info("Signal received. Triggering application shutdown...")
        asyncio.create_task(app.stop())

    # Setup OS signal handlers where supported
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, handle_signal)

    try:
        await app.start()
        # Keep running until stopped
        while app._running:
            await asyncio.sleep(0.5)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Execution interrupted by user")
    finally:
        await app.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete.")
