"""
Utility modules for Junos Push Configuration Tool

Includes connection testing, backup management, and logging utilities.
"""

import time
import logging
import socket
from datetime import datetime
from pathlib import Path
from typing import Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, TimeoutError

from jnpr.junos import Device
from jnpr.junos.exception import ConnectError
from rich.console import Console
from rich.logging import RichHandler

console = Console()

def setup_logging(verbose: bool = False):
    """Setup logging configuration with Rich handler."""
    level = logging.DEBUG if verbose else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)]
    )

class ConnectionTester:
    """Tests network connectivity to Juniper devices."""

    def __init__(self, credentials: dict, timeout: int = 60):
        self.credentials = credentials
        self.timeout = timeout

    def test_connection(self, device_ip: str) -> Tuple[bool, float]:
        """
        Test connection to a device.

        Args:
            device_ip: IP address of the device

        Returns:
            Tuple of (success, response_time)
        """
        start_time = time.time()

        try:
            # First test basic TCP connectivity
            if not self._test_tcp_connection(device_ip, 22):
                return False, 0.0

            # Then test Junos device connection
            with Device(host=device_ip, **self.credentials, auto_probe=self.timeout) as dev:
                # Simple test - get device facts
                dev.facts_refresh()
                response_time = time.time() - start_time
                return True, response_time

        except ConnectError:
            return False, 0.0
        except Exception as e:
            console.print(f"⚠️  [yellow]Connection test warning for {device_ip}: {str(e)}[/yellow]")
            return False, 0.0

    def _test_tcp_connection(self, host: str, port: int, timeout: int = 10) -> bool:
        """Test basic TCP connectivity to host:port."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
        except Exception:
            return False

class BackupManager:
    """Manages configuration backups for Juniper devices."""

    def __init__(self, backup_dir: str = "backups"):
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(exist_ok=True)

    def create_backup(self, device: Device, device_ip: str) -> bool:
        """
        Create a configuration backup for a device.

        Args:
            device: Connected Junos device object
            device_ip: IP address of the device

        Returns:
            True if backup was successful, False otherwise
        """
        try:
            # Generate backup filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{device_ip}_{timestamp}.conf"
            backup_path = self.backup_dir / filename

            # Get current configuration
            config = device.rpc.get_config(options={'format': 'set'})

            # Save to backup file
            with open(backup_path, 'w') as f:
                f.write(config.text)

            console.print(f"💾 [green]Backup created: {backup_path}[/green]")
            return True

        except Exception as e:
            console.print(f"❌ [red]Failed to create backup for {device_ip}: {str(e)}[/red]")
            return False

    def list_backups(self, device_ip: Optional[str] = None) -> list:
        """
        List available backups.

        Args:
            device_ip: Filter backups by device IP (optional)

        Returns:
            List of backup file paths
        """
        pattern = f"{device_ip}_*.conf" if device_ip else "*.conf"
        return list(self.backup_dir.glob(pattern))

    def cleanup_old_backups(self, device_ip: str, keep_count: int = 10) -> int:
        """
        Clean up old backup files, keeping only the most recent ones.

        Args:
            device_ip: IP address of the device
            keep_count: Number of backups to keep

        Returns:
            Number of backups deleted
        """
        backups = sorted(self.list_backups(device_ip), key=lambda x: x.stat().st_mtime, reverse=True)

        deleted_count = 0
        for backup in backups[keep_count:]:
            try:
                backup.unlink()
                deleted_count += 1
            except Exception as e:
                console.print(f"⚠️  [yellow]Failed to delete old backup {backup}: {str(e)}[/yellow]")

        if deleted_count > 0:
            console.print(f"🧹 [cyan]Cleaned up {deleted_count} old backup(s) for {device_ip}[/cyan]")

        return deleted_count

class ProgressTracker:
    """Tracks progress of operations across multiple devices."""

    def __init__(self):
        self.operations = {}
        self.start_time = None

    def start_operation(self, operation_id: str, description: str, total_steps: int = 1):
        """Start tracking an operation."""
        self.operations[operation_id] = {
            'description': description,
            'total_steps': total_steps,
            'completed_steps': 0,
            'start_time': time.time(),
            'status': 'running'
        }
        if self.start_time is None:
            self.start_time = time.time()

    def update_progress(self, operation_id: str, steps: int = 1):
        """Update progress for an operation."""
        if operation_id in self.operations:
            self.operations[operation_id]['completed_steps'] += steps

    def complete_operation(self, operation_id: str, status: str = 'completed'):
        """Mark an operation as completed."""
        if operation_id in self.operations:
            self.operations[operation_id]['status'] = status
            self.operations[operation_id]['end_time'] = time.time()

    def get_overall_progress(self) -> dict:
        """Get overall progress statistics."""
        total_ops = len(self.operations)
        completed_ops = sum(1 for op in self.operations.values() if op['status'] in ['completed', 'failed'])

        return {
            'total_operations': total_ops,
            'completed_operations': completed_ops,
            'progress_percentage': (completed_ops / total_ops * 100) if total_ops > 0 else 0,
            'elapsed_time': time.time() - self.start_time if self.start_time else 0
        }

class ConfigDiffer:
    """Utility to compare configurations and show differences."""

    @staticmethod
    def compare_configs(config1: str, config2: str) -> dict:
        """
        Compare two configurations and return differences.

        Args:
            config1: First configuration
            config2: Second configuration

        Returns:
            Dictionary with difference information
        """
        lines1 = set(config1.strip().split('\n'))
        lines2 = set(config2.strip().split('\n'))

        return {
            'added_lines': list(lines2 - lines1),
            'removed_lines': list(lines1 - lines2),
            'common_lines': list(lines1 & lines2),
            'total_changes': len(lines1.symmetric_difference(lines2))
        }

class DeviceInfoCollector:
    """Collects device information for reporting and validation."""

    @staticmethod
    def collect_device_info(device: Device) -> dict:
        """
        Collect device information.

        Args:
            device: Connected Junos device

        Returns:
            Dictionary with device information
        """
        try:
            facts = device.facts
            return {
                'hostname': facts.get('hostname', 'unknown'),
                'model': facts.get('model', 'unknown'),
                'version': facts.get('version', 'unknown'),
                'serial_number': facts.get('serialnumber', 'unknown'),
                'uptime': facts.get('RE0', {}).get('up_time', 'unknown'),
                'last_reboot_reason': facts.get('RE0', {}).get('last_reboot_reason', 'unknown')
            }
        except Exception as e:
            console.print(f"⚠️  [yellow]Could not collect device info: {str(e)}[/yellow]")
            return {}
