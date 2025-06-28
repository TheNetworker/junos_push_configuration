"""
Core Junos Push Configuration Engine

Handles device connections, configuration validation, and push operations.
"""

import configparser
import time
import threading
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from jnpr.junos import Device
from jnpr.junos.utils.config import Config
from jnpr.junos.exception import ConnectError, LockError, UnlockError, CommitError, RpcError
from jnpr.junos.utils.start_shell import StartShell
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.table import Table
from rich.panel import Panel
from pprint import pprint
from rich.text import Text
from rich.live import Live
from rich.layout import Layout
from lxml import etree

from .validators import ConfigValidator
from .utils import ConnectionTester, BackupManager

console = Console()


class JunosPushTool:
    """Main class for Junos configuration push operations."""

    def __init__(self, config_ini_path: str = "config.ini", timeout: int = 60, verbose: bool = False):
        self.config_ini_path = config_ini_path
        self.timeout = timeout
        self.verbose = verbose
        self.credentials = {}
        self.device_groups = {}
        self.validator = ConfigValidator()
        self.backup_manager = BackupManager()

        self._load_config()

    def _load_config(self):
        """Load configuration from config.ini file."""
        try:
            config = configparser.ConfigParser()
            config.read(self.config_ini_path)

            # Load credentials
            if 'settings' not in config:
                raise ValueError("Missing [settings] section in config.ini")

            self.credentials = {
                'user': config['settings'].get('user'),
                'password': config['settings'].get('password')
            }

            if not self.credentials['user'] or not self.credentials['password']:
                raise ValueError("Missing user or password in [settings] section")

            # Load ignore patterns for comparison
            self.ignore_patterns = []
            if 'ignore_those_lines_in_compare' in config:
                for key, value in config['ignore_those_lines_in_compare'].items():
                    self.ignore_patterns.append(value.strip())

            if self.verbose and self.ignore_patterns:
                console.print(f"📋 Loaded {len(self.ignore_patterns)} ignore patterns for comparison")

            # Load device groups
            for section in config.sections():
                if section not in ['settings', 'ignore_those_lines_in_compare']:
                    devices = {}
                    for key, value in config[section].items():
                        devices[key] = value
                    self.device_groups[section] = devices

            if self.verbose:
                console.print(f"📋 Loaded {len(self.device_groups)} device groups from {self.config_ini_path}")

        except Exception as e:
            raise RuntimeError(f"Failed to load config.ini: {str(e)}")

    def _validate_group(self, group: str) -> List[str]:
        """Validate group exists and has exactly 2 devices."""
        if group not in self.device_groups:
            available_groups = ", ".join(self.device_groups.keys())
            raise ValueError(f"Group '{group}' not found. Available groups: {available_groups}")

        devices = list(self.device_groups[group].values())
        if len(devices) != 2:
            raise ValueError(f"Group '{group}' must have exactly 2 devices, found {len(devices)}")

        return devices

    def _test_connectivity(self, devices: List[str]) -> bool:
        """Test connectivity to all devices before proceeding."""
        console.print("\n")
        console.print("🔍 [yellow]Testing device connectivity...[/yellow]")

        tester = ConnectionTester(self.credentials, self.timeout)
        results = {}

        with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True
        ) as progress:

            def test_device(device_ip):
                task = progress.add_task(f"Testing {device_ip}...", total=None)
                result = tester.test_connection(device_ip)
                progress.remove_task(task)
                return device_ip, result

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(test_device, device) for device in devices]

                for future in as_completed(futures):
                    device_ip, result = future.result()
                    results[device_ip] = result

        # Display results
        table = Table(title="🌐 Connectivity Test Results")
        table.add_column("Device", style="cyan")
        table.add_column("Status", style="bold")
        table.add_column("Response Time", style="green")

        all_reachable = True
        for device_ip in devices:
            status, response_time = results[device_ip]
            if status:
                table.add_row(device_ip, "✅ Reachable", f"{response_time:.2f}s")
            else:
                table.add_row(device_ip, "❌ Unreachable", "N/A")
                all_reachable = False

        console.print(table)

        if not all_reachable:
            console.print("❌ [bold red]Not all devices are reachable. Aborting operation.[/bold red]")
            return False

        console.print("✅ [green]All devices are reachable![/green]")
        return True

    def _check_pending_config(self, device_ip: str) -> bool:
        """Check for pending/uncommitted configuration on a device."""
        try:
            with Device(host=device_ip, **self.credentials, auto_probe=self.timeout) as dev:
                # Check for pending configuration

                # result = dev.rpc.get_configuration({"format": "text", "compare": "rollback", "rollback": "0"})
                result = dev.rpc.get_config(options={'database': 'candidate', 'format': 'set'})
                result = result.text.splitlines()
                result_comitted = dev.rpc.get_config(options={'database': 'committed', 'format': 'set'})
                result_comitted = result_comitted.text.splitlines()
                # print(result.text)
                # print("--------------------------------------------------")
                # print(result_comitted.text)

                # Check if there is any difference between candidate and committed configuration
                if result and result_comitted:
                    pending_lines = set(result) - set(result_comitted)
                    if pending_lines:
                        console.print(f"⚠️  [yellow]Pending configuration found on {device_ip}:[/yellow]")
                        for line in pending_lines:
                            print(f"   • {line}")
                        return True
                else:
                    console.print(f"✅ [green]No pending configuration on {device_ip}[/green]")
                return False
        except Exception as e:
            console.print(f"⚠️  [yellow]Warning: Could not check pending config on {device_ip}: {str(e)}[/yellow]")
            return False

    def _validate_device_state(self, devices: List[str]) -> bool:
        """Validate that devices have no pending configuration."""
        console.print("🔍 [yellow]Checking for pending configurations...[/yellow]")

        pending_devices = []

        with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True
        ) as progress:

            def check_device(device_ip):
                task = progress.add_task(f"Checking {device_ip}...", total=None)
                has_pending = self._check_pending_config(device_ip)
                progress.remove_task(task)
                return device_ip, has_pending

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(check_device, device) for device in devices]

                for future in as_completed(futures):
                    device_ip, has_pending = future.result()
                    if has_pending:
                        pending_devices.append(device_ip)

        if pending_devices:
            console.print(
                f"❌ [bold red]Found pending configuration on devices: {', '.join(pending_devices)}[/bold red]")
            console.print("🧹 [yellow]Please clean uncommitted configuration before proceeding.[/yellow]")
            return False

        console.print("✅ [green]No pending configurations found![/green]")
        return True

    def _push_config_to_device(self, device_ip: str, config_content: str, operation: str, backup: bool = False) -> \
    Tuple[bool, str]:
        """Push configuration to a single device."""
        try:
            with Device(host=device_ip, **self.credentials, auto_probe=self.timeout) as dev:

                # Create backup if requested
                if backup:
                    backup_result = self.backup_manager.create_backup(dev, device_ip)
                    if not backup_result:
                        return False, "Failed to create backup"

                # Lock configuration
                with Config(dev, mode='private') as cu:

                    if operation == 'rollback':
                        # Convert set commands to delete commands for targeted rollback
                        delete_config = self._convert_set_to_delete(config_content)
                        if not delete_config.strip():
                            return False, "No valid set commands found to convert to delete commands"

                        # Load the delete configuration
                        cu.load(delete_config, format='set')

                        # Commit the delete operations
                        cu.commit(ignore_warning=True)
                        return True, f"Configuration rollback completed - deleted {len(delete_config.splitlines())} configuration lines"

                    # Load configuration
                    cu.load(config_content, format='set')

                    if operation == 'check':
                        # Just check the configuration
                        if cu.commit_check():
                            return True, "Configuration check passed"
                        else:
                            return False, "Configuration check failed"

                    elif operation == 'commit':
                        # Commit the configuration
                        cu.commit(ignore_warning=True)
                        return True, "Configuration committed successfully"

                    elif operation == 'commit-confirmed':
                        # Commit with confirmation (5 minutes)
                        cu.commit(ignore_warning=True, confirm=5)
                        return True, "Configuration committed with confirmation"



        except (ConnectError, LockError, CommitError, RpcError) as e:
            return False, f"Junos error: {str(e)}"
        except Exception as e:
            return False, f"Unexpected error: {str(e)}"

    def _filter_config_lines(self, config_lines: List[str]) -> List[str]:
        """
        Filter out configuration lines that should be ignored during comparison.

        Args:
            config_lines: List of configuration lines

        Returns:
            Filtered list of configuration lines
        """
        if not self.ignore_patterns:
            return config_lines

        filtered_lines = []
        ignored_count = 0

        for line in config_lines:
            line_stripped = line.strip()
            should_ignore = False

            # Check if line matches any ignore pattern
            for pattern in self.ignore_patterns:
                if pattern in line_stripped:
                    should_ignore = True
                    ignored_count += 1
                    break

            if not should_ignore:
                filtered_lines.append(line)

        if self.verbose and ignored_count > 0:
            console.print(f"🚫 [yellow]Ignored {ignored_count} lines matching ignore patterns[/yellow]")

        return filtered_lines

    def _normalize_config_line(self, line: str) -> str:
        """
        Normalize configuration line by replacing device-specific values with placeholders.

        Args:
            line: Configuration line to normalize

        Returns:
            Normalized configuration line
        """
        import re

        normalized_line = line

        for pattern in self.normalize_patterns:
            if pattern == 'ip_addresses':
                # Replace IPv4 addresses with placeholder
                normalized_line = re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b',
                                         '<IP_ADDRESS>', normalized_line)
                # Replace IPv6 addresses with placeholder
                normalized_line = re.sub(r'\b(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}(?:/\d{1,3})?\b',
                                         '<IPV6_ADDRESS>', normalized_line)

            elif pattern == 'interface_numbers':
                # Replace interface numbers (ge-0/0/1, xe-1/2/3, etc.)
                normalized_line = re.sub(r'(ge|xe|et|ae|irb|lo|fxp|em|vlan)-(\d+/\d+/\d+|\d+)',
                                         r'\1-<INTERFACE_NUMBER>', normalized_line)

            elif pattern == 'hostnames':
                # Replace common hostname patterns
                normalized_line = re.sub(r'\b[a-zA-Z0-9]+-[0-9]+\b', '<HOSTNAME>', normalized_line)
                normalized_line = re.sub(r'\brouter[0-9]+\b', '<HOSTNAME>', normalized_line)
                normalized_line = re.sub(r'\bswitch[0-9]+\b', '<HOSTNAME>', normalized_line)

            elif pattern == 'as_numbers':
                # Replace AS numbers
                normalized_line = re.sub(r'\bas (\d+)\b', 'as <AS_NUMBER>', normalized_line)
                normalized_line = re.sub(r'\bpeer-as (\d+)\b', 'peer-as <AS_NUMBER>', normalized_line)

            elif pattern == 'vlan_ids':
                # Replace VLAN IDs
                normalized_line = re.sub(r'\bvlan-id (\d+)\b', 'vlan-id <VLAN_ID>', normalized_line)
                normalized_line = re.sub(r'\bvlan (\d+)\b', 'vlan <VLAN_ID>', normalized_line)

        return normalized_line

    def _find_similar_lines(self, lines1: set, lines2: set) -> Tuple[List[Tuple[str, str]], set, set]:
        """
        Find lines that are similar between two sets but not exactly identical.

        Args:
            lines1: First set of configuration lines
            lines2: Second set of configuration lines

        Returns:
            Tuple of (similar_pairs, truly_unique_lines1, truly_unique_lines2)
        """
        # Create normalized versions of all lines
        normalized1 = {line: self._normalize_config_line(line) for line in lines1}
        normalized2 = {line: self._normalize_config_line(line) for line in lines2}

        # Find lines with same normalized form
        similar_pairs = []
        matched_lines1 = set()
        matched_lines2 = set()

        for line1, norm1 in normalized1.items():
            for line2, norm2 in normalized2.items():
                if norm1 == norm2 and line1 != line2:  # Same normalized form but different actual lines
                    similar_pairs.append((line1, line2))
                    matched_lines1.add(line1)
                    matched_lines2.add(line2)
                    break  # Only match each line once

        # Lines that are truly unique (no similar counterpart)
        truly_unique_lines1 = lines1 - matched_lines1
        truly_unique_lines2 = lines2 - matched_lines2

        return similar_pairs, truly_unique_lines1, truly_unique_lines2

    def _highlight_differences_in_line(self, line1: str, line2: str) -> Tuple[str, str]:
        """
        Highlight the specific differences between two similar lines.

        Args:
            line1: First line
            line2: Second line

        Returns:
            Tuple of highlighted versions of both lines
        """
        import difflib

        # Split lines into words for better comparison
        words1 = line1.split()
        words2 = line2.split()

        # Use difflib to find differences
        matcher = difflib.SequenceMatcher(None, words1, words2)

        highlighted1 = []
        highlighted2 = []

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                highlighted1.extend(words1[i1:i2])
                highlighted2.extend(words2[j1:j2])
            elif tag == 'delete':
                highlighted1.extend([f"[bold red]{word}[/bold red]" for word in words1[i1:i2]])
            elif tag == 'insert':
                highlighted2.extend([f"[bold green]{word}[/bold green]" for word in words2[j1:j2]])
            elif tag == 'replace':
                highlighted1.extend([f"[bold red]{word}[/bold red]" for word in words1[i1:i2]])
                highlighted2.extend([f"[bold green]{word}[/bold green]" for word in words2[j1:j2]])

        return ' '.join(highlighted1), ' '.join(highlighted2)

    def _find_similar_lines_advanced(self, lines1: set, lines2: set) -> Tuple[List[Tuple[str, str]], set, set]:
        """
        Find lines that are similar between two sets using advanced similarity detection.

        Args:
            lines1: First set of configuration lines
            lines2: Second set of configuration lines

        Returns:
            Tuple of (similar_pairs, truly_unique_lines1, truly_unique_lines2)
        """
        try:
            from Levenshtein import ratio
        except ImportError:
            # Fallback to basic difflib if Levenshtein is not available
            import difflib
            def ratio(a, b):
                return difflib.SequenceMatcher(None, a, b).ratio()

        similar_pairs = []
        matched_lines1 = set()
        matched_lines2 = set()

        # Convert sets to lists for iteration
        list1 = list(lines1)
        list2 = list(lines2)

        # Find similar lines using string similarity
        for line1 in list1:
            best_match = None
            best_ratio = 0.0

            for line2 in list2:
                if line2 in matched_lines2:  # Skip already matched lines
                    continue

                # Calculate similarity ratio
                similarity = ratio(line1, line2)

                # Only consider as similar if:
                # 1. Similarity is high (85-99%) but not perfect (100%)
                # 2. Lines are not identical
                if 0.85 <= similarity < 1.0 and line1 != line2:
                    if similarity > best_ratio:
                        best_ratio = similarity
                        best_match = line2

            # If we found a good match, record it
            if best_match and best_ratio >= 0.85:
                similar_pairs.append((line1, best_match))
                matched_lines1.add(line1)
                matched_lines2.add(best_match)

        # Lines that are truly unique (no similar counterpart)
        truly_unique_lines1 = lines1 - matched_lines1
        truly_unique_lines2 = lines2 - matched_lines2

        return similar_pairs, truly_unique_lines1, truly_unique_lines2

    def _compare_device_configs(self, devices: List[str]) -> bool:
        """
        Compare configurations between two devices and display differences.

        Args:
            devices: List of device IP addresses (should be exactly 2)

        Returns:
            True if comparison was successful, False otherwise
        """
        if len(devices) != 2:
            console.print("❌ [red]Compare operation requires exactly 2 devices[/red]")
            return False

        device1_ip, device2_ip = devices

        try:
            # Display ignore patterns if any
            if self.ignore_patterns:
                console.print(f"🚫 [yellow]Ignoring lines containing patterns:[/yellow]")
                for pattern in self.ignore_patterns:
                    console.print(f"   • {pattern}")
                console.print()

            # Get configurations from both devices
            console.print("📋 [yellow]Retrieving configurations from devices...[/yellow]")

            with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    transient=True
            ) as progress:

                def get_device_config(device_ip):
                    task = progress.add_task(f"Getting config from {device_ip}...", total=None)
                    try:
                        with Device(host=device_ip, **self.credentials, auto_probe=self.timeout) as dev:
                            config = dev.rpc.get_config(options={'format': 'set'})
                            config_lines = [line.strip() for line in config.text.splitlines()
                                            if line.strip() and not line.strip().startswith('#')]

                            # Filter out ignored lines
                            filtered_lines = self._filter_config_lines(config_lines)

                            progress.remove_task(task)
                            return device_ip, filtered_lines, None
                    except Exception as e:
                        progress.remove_task(task)
                        return device_ip, [], str(e)

                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [executor.submit(get_device_config, device) for device in devices]
                    results = {}
                    errors = {}

                    for future in as_completed(futures):
                        device_ip, config_lines, error = future.result()
                        if error:
                            errors[device_ip] = error
                        else:
                            results[device_ip] = config_lines

            # Check for errors
            if errors:
                for device_ip, error in errors.items():
                    console.print(f"❌ [red]Failed to get config from {device_ip}: {error}[/red]")
                return False

            # Compare configurations with accurate similarity detection
            config1 = set(results[device1_ip])
            config2 = set(results[device2_ip])

            # Calculate exact matches first
            common_lines = config1 & config2

            # Find truly unique lines (after removing common ones)
            remaining_lines1 = config1 - common_lines
            remaining_lines2 = config2 - common_lines

            # Find similar lines among the remaining ones
            similar_pairs, truly_unique1, truly_unique2 = self._find_similar_lines_advanced(
                remaining_lines1, remaining_lines2
            )

            # Display accurate comparison results
            self._display_accurate_config_comparison(
                device1_ip, device2_ip,
                truly_unique1, truly_unique2, similar_pairs, common_lines,
                len(config1), len(config2)
            )

            return True

        except Exception as e:
            console.print(f"❌ [red]Configuration comparison failed: {str(e)}[/red]")
            return False

    def _display_accurate_config_comparison(self, device1_ip: str, device2_ip: str,
                                            truly_unique1: set, truly_unique2: set,
                                            similar_pairs: List[Tuple[str, str]], common_lines: set,
                                            total1: int, total2: int):
        """
        Display accurate configuration comparison results.

        Args:
            device1_ip: IP of first device
            device2_ip: IP of second device
            truly_unique1: Truly unique lines only in device 1
            truly_unique2: Truly unique lines only in device 2
            similar_pairs: List of similar line pairs between devices
            common_lines: Lines common to both devices
            total1: Total lines in device 1
            total2: Total lines in device 2
        """
        console.print("\n" + "=" * 100)
        console.print(f"🔍 [bold cyan]Accurate Configuration Comparison Results[/bold cyan]")
        console.print("=" * 100)

        # Summary statistics
        summary_table = Table(title="📊 Accurate Comparison Summary", expand=True)
        summary_table.add_column("Metric", style="cyan", width=30)
        summary_table.add_column(f"{device1_ip}", style="blue", width=20)
        summary_table.add_column(f"{device2_ip}", style="blue", width=20)
        summary_table.add_column("Status", style="bold", width=20)

        summary_table.add_row(
            "Total Configuration Lines",
            str(total1),
            str(total2),
            "✅ Match" if total1 == total2 else "⚠️ Different"
        )

        summary_table.add_row(
            "Identical Lines",
            str(len(common_lines)),
            str(len(common_lines)),
            "📋 Exact Match"
        )

        summary_table.add_row(
            "Unique Lines",
            str(len(truly_unique1)),
            str(len(truly_unique2)),
            "🔍 True Differences"
        )

        summary_table.add_row(
            "Similar Lines (85-99%)",
            str(len(similar_pairs)),
            str(len(similar_pairs)),
            "🔗 Device-Specific"
        )

        # Show ignore patterns summary if any were used
        if self.ignore_patterns:
            summary_table.add_row(
                "Ignored Patterns",
                str(len(self.ignore_patterns)),
                str(len(self.ignore_patterns)),
                "🚫 Filtered"
            )

        console.print(summary_table)
        console.print()

        # If configurations are functionally identical
        if not truly_unique1 and not truly_unique2:
            console.print("🎉 [bold green]Configurations are functionally identical![/bold green]")
            console.print(f"📋 [cyan]Both devices have {len(common_lines)} identical configuration lines[/cyan]")
            if similar_pairs:
                console.print(
                    f"🔗 [blue]{len(similar_pairs)} lines differ only in device-specific values (IP addresses, etc.)[/blue]")
            if self.ignore_patterns:
                console.print(f"🚫 [yellow]({len(self.ignore_patterns)} ignore patterns were applied)[/yellow]")
            return

        # Display differences in full-width format
        console.print("🔍 [bold yellow]Configuration Differences[/bold yellow]")
        console.print("=" * 100)

        # Display truly unique lines in device 1
        if truly_unique1:
            console.print(
                f"\n❌ [bold red]Unique Configurations in {device1_ip} ({len(truly_unique1)} total):[/bold red]")
            console.print("-" * 100)

            sorted_device1_unique = sorted(truly_unique1)
            for i, line in enumerate(sorted_device1_unique[:30], 1):  # Show first 30 lines
                console.print(f"[red]{i:3d}. - {line}[/red]")

            if len(sorted_device1_unique) > 30:
                console.print(f"[red]     ... and {len(sorted_device1_unique) - 30} more lines[/red]")

        # Display truly unique lines in device 2
        if truly_unique2:
            console.print(
                f"\n✅ [bold green]Unique Configurations in {device2_ip} ({len(truly_unique2)} total):[/bold green]")
            console.print("-" * 100)

            sorted_device2_unique = sorted(truly_unique2)
            for i, line in enumerate(sorted_device2_unique[:30], 1):  # Show first 30 lines
                console.print(f"[green]{i:3d}. + {line}[/green]")

            if len(sorted_device2_unique) > 30:
                console.print(f"[green]     ... and {len(sorted_device2_unique) - 30} more lines[/green]")

        # Display similar lines (device-specific variations)
        if similar_pairs:
            console.print(
                f"\n🔗 [bold blue]Similar Lines - Device-Specific Variations ({len(similar_pairs)} pairs):[/bold blue]")
            console.print("[blue]These lines are functionally similar but differ in device-specific values:[/blue]")
            console.print("-" * 100)

            for i, (line1, line2) in enumerate(similar_pairs[:15], 1):  # Show first 15 pairs
                highlighted1, highlighted2 = self._highlight_differences_in_line(line1, line2)
                console.print(f"[blue]{i:3d}. {device1_ip}: {highlighted1}[/blue]")
                console.print(f"[blue]     {device2_ip}: {highlighted2}[/blue]")
                console.print()

            if len(similar_pairs) > 15:
                console.print(f"[blue]     ... and {len(similar_pairs) - 15} more similar pairs[/blue]")

        # Configuration suggestions
        console.print(f"\n💡 [bold cyan]Analysis & Suggestions:[/bold cyan]")
        console.print("-" * 100)

        if truly_unique1 or truly_unique2:
            console.print(
                f"   🔍 [yellow]Found {len(truly_unique1) + len(truly_unique2)} truly different configuration lines[/yellow]")
            if truly_unique1:
                console.print(
                    f"   • To sync {device2_ip} with {device1_ip}: Apply the [red]{len(truly_unique1)} missing configurations[/red] shown above")
            if truly_unique2:
                console.print(
                    f"   • To sync {device1_ip} with {device2_ip}: Apply the [green]{len(truly_unique2)} missing configurations[/green] shown above")

        if similar_pairs:
            console.print(
                f"   🔗 [blue]Found {len(similar_pairs)} device-specific variations (expected differences)[/blue]")
            console.print(f"   • These are normal differences due to device-specific values (IPs, hostnames, etc.)")

        console.print(f"   • Use 'rollback' operation to remove unwanted configurations")
        console.print(f"   • Use 'commit' operation to add missing configurations")
        console.print(f"   • Create configuration files from the true differences above for targeted deployments")

    def _convert_set_to_delete(self, config_content: str) -> str:
        """
        Convert 'set' commands to 'delete' commands for rollback operations.

        Args:
            config_content: Configuration content with 'set' commands

        Returns:
            Configuration content with 'delete' commands
        """
        delete_lines = []

        for line in config_content.strip().split('\n'):
            line = line.strip()

            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue

            # Convert 'set' to 'delete'
            if line.lower().startswith('set '):
                delete_line = 'delete ' + line[4:]  # Replace 'set ' with 'delete '
                delete_lines.append(delete_line)
            elif line.lower().startswith('delete '):
                # If it's already a delete command, convert to set (reverse operation)
                set_line = 'set ' + line[7:]  # Replace 'delete ' with 'set '
                delete_lines.append(set_line)
            else:
                # For other commands (activate, deactivate, etc.), keep as is
                # but log a warning
                console.print(f"⚠️  [yellow]Warning: Skipping non-set command in rollback: {line}[/yellow]")

        return '\n'.join(delete_lines)

    def _validate_configuration_status(self, devices: List[str], config_content: str) -> Dict[str, Dict]:
        """
        Validate configuration status across all devices.
        
        Args:
            devices: List of device IP addresses
            config_content: Configuration content to validate
            
        Returns:
            Dictionary with configuration status for each device
        """
        console.print("\n")
        console.print("🔍 [yellow]Checking existing configurations...[/yellow]")

        results = {}

        with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True
        ) as progress:
            def check_device_config(device_ip):
                task = progress.add_task(f"Analyzing {device_ip}...", total=None)
                has_existing, existing_lines, new_lines = self._check_existing_config(device_ip, config_content)
                progress.remove_task(task)
                return device_ip, {
                    'has_existing': has_existing,
                    'existing_lines': existing_lines,
                    'new_lines': new_lines,
                    'total_proposed': len(existing_lines) + len(new_lines),
                    'existing_count': len(existing_lines),
                    'new_count': len(new_lines)
                }

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(check_device_config, device) for device in devices]

                for future in as_completed(futures):
                    device_ip, config_status = future.result()
                    results[device_ip] = config_status

        # Display configuration analysis results
        self._display_config_analysis(results)

        return results

    def _check_existing_config(self, device_ip: str, config_content: str) -> Tuple[bool, List[str], List[str]]:
        """
        Check if the provided configuration already exists on the device.
        
        Args:
            device_ip: IP address of the device
            config_content: Configuration content to check
            
        Returns:
            Tuple of (has_existing_config, existing_lines, new_lines)
        """
        try:
            with Device(host=device_ip, **self.credentials, auto_probe=self.timeout) as dev:
                # Get current configuration in set format
                current_config = dev.rpc.get_config(options={'format': 'set'})
                current_lines = set()

                if current_config.text:
                    # Parse current configuration lines
                    for line in current_config.text.strip().split('\n'):
                        line = line.strip()
                        if line and not line.startswith('#'):
                            current_lines.add(line)

                # Parse proposed configuration lines
                proposed_lines = set()
                for line in config_content.strip().split('\n'):
                    line = line.strip()
                    if line and not line.startswith('#'):
                        proposed_lines.add(line)

                # Find overlapping and new configurations
                existing_lines = list(proposed_lines.intersection(current_lines))
                new_lines = list(proposed_lines - current_lines)

                has_existing = len(existing_lines) > 0

                return has_existing, existing_lines, new_lines

        except Exception as e:
            console.print(f"⚠️  [yellow]Warning: Could not check existing config on {device_ip}: {str(e)}[/yellow]")
            return False, [], []

    def _display_config_analysis(self, results: Dict[str, Dict]):
        """Display configuration analysis results in a formatted table."""

        table = Table(title="📋 Configuration Analysis Results")
        table.add_column("Device", style="cyan", width=20)
        table.add_column("Total Lines", style="white", width=12)
        table.add_column("Already Present", style="yellow", width=15)
        table.add_column("New Lines", style="green", width=12)
        table.add_column("Status", style="bold", width=20)

        for device_ip, status in results.items():
            total = status['total_proposed']
            existing = status['existing_count']
            new = status['new_count']

            if existing == total and total > 0:
                status_text = "🟡 Fully Present"
            elif existing > 0 and new > 0:
                status_text = "🔵 Partially Present"
            elif new == total:
                status_text = "🟢 All New"
            else:
                status_text = "⚪ No Changes"

            table.add_row(
                device_ip,
                str(total),
                f"{existing} ({(existing / total * 100):.0f}%)" if total > 0 else "0",
                str(new),
                status_text
            )

        console.print(table)

    def _evaluate_config_status(self, config_status: Dict[str, Dict], operation: str) -> bool:
        """
        Evaluate whether to proceed based on configuration status.
        
        Args:
            config_status: Configuration status results from validation
            operation: The operation being performed
            
        Returns:
            True if should proceed, False otherwise
        """
        # Check if all configurations are already fully present
        all_fully_present = all(
            status['existing_count'] == status['total_proposed'] and status['total_proposed'] > 0
            for status in config_status.values()
        )

        # Check if any configurations are present
        any_existing_config = any(status['has_existing'] for status in config_status.values())

        if all_fully_present:
            if operation in ['commit', 'commit-confirmed']:
                console.print("\n⚠️  [bold yellow]All configurations are already present on all devices![/bold yellow]")
                console.print("📋 [cyan]Summary of findings:[/cyan]")

                for device_ip, status in config_status.items():
                    existing_pct = (status['existing_count'] / status['total_proposed'] * 100) if status[
                                                                                                      'total_proposed'] > 0 else 0
                    console.print(
                        f"   • {device_ip}: {status['existing_count']}/{status['total_proposed']} lines present ({existing_pct:.0f}%)")

                console.print(
                    "\n🤔 [yellow]Do you want to continue anyway? This will result in no configuration changes.[/yellow]")
                console.print("⚡ [cyan]Proceeding with operation (no actual changes will occur)...[/cyan]")
                return True

            elif operation == 'check':
                console.print(
                    "\n✅ [green]Configuration check shows all lines already present - this is expected for validation.[/green]")
                return True

        elif any_existing_config:
            console.print(f"\n📊 [cyan]Configuration Analysis Summary:[/cyan]")

            total_new_lines = sum(status['new_count'] for status in config_status.values())
            total_existing_lines = sum(status['existing_count'] for status in config_status.values())

            console.print(f"   • Total new configuration lines: {total_new_lines}")
            console.print(f"   • Total existing configuration lines: {total_existing_lines}")

            if total_new_lines > 0:
                console.print(f"   ✨ [green]Will apply {total_new_lines} new configuration lines[/green]")

            if total_existing_lines > 0:
                console.print(f"   📝 [yellow]Skipping {total_existing_lines} already present lines[/yellow]")

        else:
            console.print("\n✨ [green]All configurations are new - proceeding with fresh deployment[/green]")

        return True

    def _execute_parallel(self, devices: List[str], config_content: str, operation: str, backup: bool) -> Dict[
        str, Tuple[bool, str]]:
        """Execute operations on devices in parallel."""
        results = {}

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {
                executor.submit(self._push_config_to_device, device, config_content, operation, backup): device
                for device in devices
            }

            for future in as_completed(futures):
                device = futures[future]
                try:
                    success, message = future.result()
                    results[device] = (success, message)
                except Exception as e:
                    results[device] = (False, f"Execution error: {str(e)}")

        return results

    def _execute_sequential(self, devices: List[str], config_content: str, operation: str, backup: bool) -> Dict[
        str, Tuple[bool, str]]:
        """Execute operations on devices sequentially."""
        results = {}

        for device in devices:
            console.print(f"🔄 [cyan]Processing device: {device}[/cyan]")

            with Progress(
                    SpinnerColumn(),
                    TextColumn(f"Executing {operation} on {device}..."),
                    transient=True
            ) as progress:
                task = progress.add_task("Processing...", total=None)
                success, message = self._push_config_to_device(device, config_content, operation, backup)
                progress.remove_task(task)

            results[device] = (success, message)

            if success:
                console.print(f"✅ [green]{device}: {message}[/green]")
            else:
                console.print(f"❌ [red]{device}: {message}[/red]")

            # Add small delay between devices for sequential execution
            time.sleep(1)

        return results

    def _display_final_results(self, results: Dict[str, Tuple[bool, str]], operation: str):
        """Display the final results of the operation."""
        table = Table(title=f"📊 Operation Results - {operation.upper()}")
        table.add_column("Device", style="cyan", width=20)
        table.add_column("Status", style="bold", width=12)
        table.add_column("Message", style="white", width=50)

        success_count = 0
        for device, (success, message) in results.items():
            if success:
                table.add_row(device, "✅ Success", message)
                success_count += 1
            else:
                table.add_row(device, "❌ Failed", message)

        console.print(table)

        # Summary
        total_devices = len(results)
        if success_count == total_devices:
            console.print(f"🎉 [bold green]All {total_devices} devices completed successfully![/bold green]")
        else:
            failed_count = total_devices - success_count
            console.print(
                f"⚠️  [yellow]{success_count}/{total_devices} devices succeeded, {failed_count} failed[/yellow]")

    def execute(self, group: str, config_file: str, operation: str, dry_run: bool = False,
                parallel: bool = False, backup: bool = False) -> bool:
        """Execute the main operation."""

        try:
            # Validate group and get devices
            devices = self._validate_group(group)
            console.print(f"🎯 [bold cyan]Target Group: {group}[/bold cyan]")
            console.print(f"📱 [cyan]Devices: {', '.join(devices)}[/cyan]")

            # For compare operation, we don't need a config file validation
            if operation == 'compare':
                if dry_run:
                    console.print("🧪 [yellow]DRY RUN MODE - Configuration comparison would be performed[/yellow]")
                    return True

                # Test connectivity
                if not self._test_connectivity(devices):
                    return False

                # Perform comparison
                return self._compare_device_configs(devices)

            # Validate and clean configuration file for other operations
            config_content = self.validator.validate_and_clean_config(config_file)

            if dry_run:
                console.print("🧪 [yellow]DRY RUN MODE - No changes will be made[/yellow]")
                if operation == 'compare':
                    console.print(Panel("Configuration comparison would be performed between devices",
                                        title="Dry Run Results", border_style="yellow"))
                else:
                    console.print(Panel(f"Configuration would be applied to:\n{chr(10).join(devices)}",
                                        title="Dry Run Results", border_style="yellow"))
                return True

            # Test connectivity
            if not self._test_connectivity(devices):
                return False

            # Check existing configuration status (skip for compare)
            if operation != 'compare':
                config_status = self._validate_configuration_status(devices, config_content)

                # Analyze if we should proceed based on existing configurations
                should_proceed = self._evaluate_config_status(config_status, operation)
                if not should_proceed:
                    return False

            # Validate device state (no pending config) - skip for compare and rollback
            if operation not in ['rollback', 'compare'] and not self._validate_device_state(devices):
                return False

            console.print("\n")
            # Execute operation
            console.print(f"🚀 [bold yellow]Executing '{operation}' operation...[/bold yellow]")

            if parallel:
                console.print("⚡ [yellow]Running in parallel mode[/yellow]")
                results = self._execute_parallel(devices, config_content, operation, backup)
            else:
                console.print("🔄 [yellow]Running in sequential mode[/yellow]")
                results = self._execute_sequential(devices, config_content, operation, backup)

            # Display final results
            self._display_final_results(results, operation)

            # Check if all operations succeeded
            all_success = all(success for success, _ in results.values())
            return all_success

        except Exception as e:
            console.print(f"💥 [bold red]Operation failed: {str(e)}[/bold red]")
            return False
