#!/usr/bin/env python3
"""
Junos Push Configuration CLI

Main command-line interface for the Juniper network automation tool.
"""

import click
import sys
from pathlib import Path

from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from .core import JunosPushTool
from .utils import setup_logging

console = Console()

@click.command()
@click.option(
    '--group', '-g',
    required=True,
    help='Target device group from config.ini (e.g., core, edge, campus)'
)
@click.option(
    '--config-file', '-c',
    type=click.Path(exists=True),
    required=True,
    help='Path to Junos configuration file in SET format'
)
@click.option(
    '--operation', '-o',
    type=click.Choice(['check', 'commit', 'commit-confirmed', 'rollback', 'compare']),
    default='check',
    help='Operation to perform: check (commit check), commit, commit-confirmed (5 min), rollback, compare'
)
@click.option(
    '--config-ini', '-i',
    type=click.Path(exists=True),
    default='config.ini',
    help='Path to config.ini file (default: config.ini)'
)
@click.option(
    '--dry-run', '-d',
    is_flag=True,
    help='Perform a dry run without making any changes'
)
@click.option(
    '--verbose', '-v',
    is_flag=True,
    help='Enable verbose output'
)
@click.option(
    '--parallel', '-p',
    is_flag=True,
    help='Execute operations on both devices in parallel (use with caution)'
)
@click.option(
    '--backup',
    is_flag=True,
    help='Create configuration backup before applying changes'
)
@click.option(
    '--timeout',
    type=int,
    default=60,
    help='Connection timeout in seconds (default: 60)'
)
def main(group, config_file, operation, config_ini, dry_run, verbose, parallel, backup, timeout):
    """
    🚀 Junos Push Configuration Tool


    Push Junos configurations to device pairs with consistency checks and safety features.

    Examples:

    \b
    # Check configuration on core group
    junos-push -g core -c my_config.set -o check

    \b
    # Commit configuration with confirmation to edge group
    junos-push -g edge -c my_config.set -o commit-confirmed

    \b
    # Dry run on campus group
    junos-push -g campus -c my_config.set --dry-run

    \b
    # Rollback configuration on core group
    junos-push -g core -c dummy.set -o rollback
    """

    # Setup logging
    # setup_logging(verbose)

    # Display tool header
    title = Text("🌐 Junos Push Configuration Tool", style="bold cyan", )
    console.print(Align.center(Panel(title, expand=False, title_align="center")))

    try:
        # Initialize the tool
        tool = JunosPushTool(
            config_ini_path=config_ini,
            timeout=timeout,
            verbose=verbose
        )

        # Execute the operation
        success = tool.execute(
            group=group,
            config_file=config_file,
            operation=operation,
            dry_run=dry_run,
            parallel=parallel,
            backup=backup
        )

        if success:
            console.print("✅ [bold green]Operation completed successfully![/bold green]")
            sys.exit(0)
        else:
            console.print("❌ [bold red]Operation failed![/bold red]")
            sys.exit(1)

    except KeyboardInterrupt:
        console.print("\n⚠️  [yellow]Operation cancelled by user[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"💥 [bold red]Fatal error: {str(e)}[/bold red]")
        if verbose:
            console.print_exception()
        sys.exit(1)

if __name__ == '__main__':
    main()
