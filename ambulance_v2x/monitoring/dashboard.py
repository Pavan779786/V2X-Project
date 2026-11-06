"""
Terminal Dashboard Module
Interactive real-time console user interface powered by Rich:
Displays live vehicle dynamics, V2X transmission metrics, link health,
and recent operational event logs.
"""

from __future__ import annotations
import sys
import asyncio
from datetime import datetime, timezone
from typing import Optional, List, TYPE_CHECKING

try:
    from rich.console import Console
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

import config
from monitoring.health_monitor import HealthStatus

if TYPE_CHECKING:
    from data_collection.data_aggregator import DataAggregator
    from communication.rsu_client import RSUClient
    from monitoring.health_monitor import HealthMonitor


class TerminalDashboard:
    """
    Renders an ASCII/ANSI visual dashboard with vehicle instruments and V2X counters.
    """

    def __init__(
        self,
        aggregator: DataAggregator,
        rsu_client: RSUClient,
        health_monitor: HealthMonitor,
        refresh_rate_sec: float = config.DASHBOARD_REFRESH_SEC,
    ):
        self.aggregator = aggregator
        self.rsu_client = rsu_client
        self.health_monitor = health_monitor
        self.refresh_rate_sec = refresh_rate_sec

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._console = Console() if HAS_RICH else None
        self._recent_logs: List[str] = []
        self._start_time = datetime.now(timezone.utc)

    def log_event(self, text: str) -> None:
        """Add event line to the activity log tail."""
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self._recent_logs.append(f"[{ts}] {text}")
        if len(self._recent_logs) > 8:
            self._recent_logs.pop(0)

    async def start(self) -> None:
        """Start the dashboard rendering task."""
        if not HAS_RICH or not sys.stdout.isatty():
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._render_loop())

    async def stop(self) -> None:
        """Stop dashboard updating."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _render_loop(self) -> None:
        """Render loop utilizing Rich Live display."""
        if not self._console:
            return

        with Live(self._generate_layout(), console=self._console, screen=True, refresh_per_second=2) as live:
            while self._running:
                try:
                    live.update(self._generate_layout())
                    await asyncio.sleep(self.refresh_rate_sec)
                except asyncio.CancelledError:
                    break
                except Exception:
                    await asyncio.sleep(1.0)

    def _generate_layout(self) -> Layout:
        """Construct hierarchical Rich layout grid."""
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main", ratio=1),
            Layout(name="footer", size=7),
        )
        layout["main"].split_row(
            Layout(name="left_vehicle", ratio=1),
            Layout(name="right_v2x", ratio=1),
        )

        # Header Panel
        uptime = str(datetime.now(timezone.utc) - self._start_time).split(".")[0]
        status = self.health_monitor.overall_status
        status_color = "green" if status == HealthStatus.HEALTHY else ("yellow" if status == HealthStatus.DEGRADED else "red")

        header_text = Text()
        header_text.append(f" AMBULANCE V2X OBU CONTROLLER | {config.AMBULANCE_CALLSIGN} (ID: {config.STATION_ID}) ", style="bold white on blue")
        header_text.append(f"  MODE: {'SIMULATION' if config.SIMULATION_MODE else 'HARDWARE'}  |  UPTIME: {uptime}  |  STATUS: ")
        header_text.append(f" {status.value} ", style=f"bold white on {status_color}")
        layout["header"].update(Panel(header_text, style="blue"))

        # Left: Vehicle Telemetry
        vehicle_data = self.aggregator.get_latest_vehicle_data()
        left_table = Table(title="Vehicle Kinematics & Sensors", expand=True)
        left_table.add_column("Parameter", style="cyan", width=18)
        left_table.add_column("Value", style="bold yellow")

        left_table.add_row("Coordinates", f"{vehicle_data.latitude:.6f}°, {vehicle_data.longitude:.6f}°")
        left_table.add_row("Altitude", f"{vehicle_data.altitude:.1f} m")
        left_table.add_row("Speed", f"{vehicle_data.speed_kmh:.1f} km/h")
        left_table.add_row("Heading", f"{vehicle_data.heading:.1f}°")
        left_table.add_row("Acceleration", f"{vehicle_data.acceleration:.2f} m/s²")
        left_table.add_row("GPS Satellites", f"{vehicle_data.satellite_count} (HDOP: {vehicle_data.hdop:.1f})")

        siren_badge = "[bold white on red] ACTIVE [/]" if vehicle_data.siren_active else "[dim]OFF[/]"
        lights_badge = "[bold white on blue] FLASHING [/]" if vehicle_data.lights_active else "[dim]OFF[/]"
        left_table.add_row("Emergency Siren", siren_badge)
        left_table.add_row("Beacon Lights", lights_badge)
        layout["left_vehicle"].update(Panel(left_table, border_style="cyan"))

        # Right: V2X Communication & RSU Stats
        stats = self.rsu_client.stats
        right_table = Table(title="ETSI ITS-G5 / DSRC Transmission", expand=True)
        right_table.add_column("V2X Channel", style="green", width=18)
        right_table.add_column("Metric", style="bold white")

        right_table.add_row("Target RSU", f"{self.rsu_client.rsu_host}:{self.rsu_client.udp_port}")
        right_table.add_row("CAM Sent (UDP 10Hz)", f"{stats.cam_sent} pkts (Failed: {stats.cam_failed})")
        right_table.add_row("DENM Sent (TCP/UDP)", f"{stats.denm_sent} alerts (Failed: {stats.denm_failed})")
        right_table.add_row("Heartbeats Emitted", f"{stats.heartbeats_sent}")
        right_table.add_row("Total Data Emitted", f"{stats.total_bytes_sent / 1024.0:.1f} KB")
        right_table.add_row("Last Tx Latency", f"{stats.last_latency_ms:.2f} ms")

        link_status = "[green]ONLINE[/]" if self.health_monitor.components["RSU_LINK"].status == HealthStatus.HEALTHY else "[red]DISCONNECTED[/]"
        right_table.add_row("RSU Link Status", link_status)
        layout["right_v2x"].update(Panel(right_table, border_style="green"))

        # Footer: Subsystem Diagnostics & Event Log Tail
        footer_table = Table(expand=True, box=None)
        footer_table.add_column("Recent Activity Log", style="white")

        if not self._recent_logs:
            footer_table.add_row("[dim]System operational. Waiting for V2X event triggers...[/]")
        else:
            for log_line in self._recent_logs[-5:]:
                footer_table.add_row(log_line)

        layout["footer"].update(Panel(footer_table, title="Diagnostic Events", border_style="dim"))
        return layout
