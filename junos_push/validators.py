"""
Configuration Validator Module

Handles validation and cleanup of Junos configuration files.
"""

import re
import os
from pathlib import Path
from typing import List, Set
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

console = Console()

class ConfigValidator:
    """Validates and cleans Junos configuration files."""

    def __init__(self):
        self.valid_set_commands = {
            'set', 'delete', 'deactivate', 'activate', 'protect', 'unprotect'
        }

        # Common Junos configuration hierarchies
        self.valid_hierarchies = {
            'interfaces', 'protocols', 'routing-options', 'policy-options',
            'firewall', 'security', 'system', 'chassis', 'forwarding-options',
            'class-of-service', 'access', 'ethernet-switching-options',
            'vlans', 'switch-options', 'poe', 'virtual-chassis', 'snmp',
            'services', 'applications', 'groups', 'apply-groups'
        }

    def validate_and_clean_config(self, config_file: str) -> str:
        """
        Validate and clean the configuration file.

        Args:
            config_file: Path to the configuration file

        Returns:
            Cleaned configuration content as string

        Raises:
            ValueError: If configuration is invalid
        """
        console.print(f"📝 [yellow]Validating configuration file: {config_file}[/yellow]")

        # Check file exists and is readable
        config_path = Path(config_file)
        if not config_path.exists():
            raise ValueError(f"Configuration file not found: {config_file}")

        if not config_path.is_file():
            raise ValueError(f"Path is not a file: {config_file}")

        # Read file content
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError:
            # Try with different encoding
            try:
                with open(config_path, 'r', encoding='latin-1') as f:
                    content = f.read()
            except Exception as e:
                raise ValueError(f"Unable to read configuration file: {str(e)}")
        except Exception as e:
            raise ValueError(f"Error reading configuration file: {str(e)}")

        if not content.strip():
            raise ValueError("Configuration file is empty")

        # Clean and validate content
        cleaned_content = self._clean_config_content(content)
        self._validate_config_syntax(cleaned_content)

        # Display preview of cleaned configuration
        self._display_config_preview(cleaned_content)

        console.print("✅ [green]Configuration file validated successfully![/green]")
        return cleaned_content

    def _clean_config_content(self, content: str) -> str:
        """
        Clean the configuration content by removing unnecessary whitespace,
        comments, and empty lines.

        Args:
            content: Raw configuration content

        Returns:
            Cleaned configuration content
        """
        lines = content.split('\n')
        cleaned_lines = []

        for line in lines:
            # Strip leading/trailing whitespace
            line = line.strip()

            # Skip empty lines
            if not line:
                continue

            # Skip comment lines (lines starting with #)
            if line.startswith('#'):
                continue

            # Remove inline comments
            if '#' in line:
                line = line.split('#')[0].strip()
                if not line:
                    continue

            # Normalize whitespace within the line
            line = ' '.join(line.split())

            cleaned_lines.append(line)

        return '\n'.join(cleaned_lines)

    def _validate_config_syntax(self, content: str) -> None:
        """
        Validate the syntax of Junos configuration commands.

        Args:
            content: Configuration content to validate

        Raises:
            ValueError: If syntax validation fails
        """
        lines = content.split('\n')
        errors = []
        warnings = []

        for line_num, line in enumerate(lines, 1):
            if not line.strip():
                continue

            # Check if line starts with valid command
            first_word = line.split()[0].lower()
            if first_word not in self.valid_set_commands:
                errors.append(f"Line {line_num}: Invalid command '{first_word}' - must start with: {', '.join(self.valid_set_commands)}")
                continue

            # For 'set' commands, validate hierarchy
            if first_word == 'set':
                parts = line.split()
                if len(parts) < 2:
                    errors.append(f"Line {line_num}: Incomplete set command")
                    continue

                # Check for common syntax issues
                if not self._validate_set_command_syntax(line, line_num, errors, warnings):
                    continue

        # Report validation results
        if errors:
            error_text = '\n'.join(errors)
            console.print(Panel(f"❌ Configuration Validation Errors:\n\n{error_text}",
                              border_style="red", title="Syntax Errors"))
            raise ValueError(f"Configuration validation failed with {len(errors)} errors")

        if warnings:
            warning_text = '\n'.join(warnings)
            console.print(Panel(f"⚠️  Configuration Warnings:\n\n{warning_text}",
                              border_style="yellow", title="Syntax Warnings"))

    def _validate_set_command_syntax(self, line: str, line_num: int, errors: List[str], warnings: List[str]) -> bool:
        """
        Validate syntax of a set command.

        Args:
            line: The configuration line
            line_num: Line number for error reporting
            errors: List to append errors to
            warnings: List to append warnings to

        Returns:
            True if valid, False if invalid
        """
        parts = line.split()

        # Check for basic syntax issues
        if len(parts) < 3:
            errors.append(f"Line {line_num}: Set command too short - missing configuration hierarchy")
            return False

        # Check for unmatched quotes
        quote_count = line.count('"')
        if quote_count % 2 != 0:
            errors.append(f"Line {line_num}: Unmatched quotes in configuration")
            return False

        # Check for unmatched brackets
        open_brackets = line.count('[')
        close_brackets = line.count(']')
        if open_brackets != close_brackets:
            errors.append(f"Line {line_num}: Unmatched brackets in configuration")
            return False

        # Check for suspicious patterns
        if '..' in line:
            warnings.append(f"Line {line_num}: Double dots (..) in configuration path - verify this is intended")

        if line.endswith(' '):
            warnings.append(f"Line {line_num}: Trailing whitespace detected")

        # Check for common hierarchy patterns
        hierarchy = parts[1]
        if not any(hierarchy.startswith(valid_hier) for valid_hier in self.valid_hierarchies):
            warnings.append(f"Line {line_num}: Unrecognized configuration hierarchy '{hierarchy}' - verify this is correct")

        return True

    def _display_config_preview(self, content: str, max_lines: int = 20) -> None:
        """
        Display a preview of the cleaned configuration.

        Args:
            content: Configuration content to preview
            max_lines: Maximum number of lines to show in preview
        """
        lines = content.split('\n')
        total_lines = len(lines)

        if total_lines <= max_lines:
            preview_content = content
            title = f"📋 Configuration Preview ({total_lines} lines)"
        else:
            preview_lines = lines[:max_lines]
            preview_content = '\n'.join(preview_lines) + f'\n... ({total_lines - max_lines} more lines)'
            title = f"📋 Configuration Preview (showing first {max_lines} of {total_lines} lines)"

        # Syntax highlighting for Junos configuration
        syntax = Syntax(preview_content, "text", theme="monokai", line_numbers=True)
        console.print(Panel(syntax, title=title, border_style="blue"))

        # Display statistics
        stats = self._get_config_statistics(content)
        console.print(f"📊 [cyan]Configuration Statistics:[/cyan]")
        for key, value in stats.items():
            console.print(f"   • {key}: {value}")

    def _get_config_statistics(self, content: str) -> dict:
        """
        Get statistics about the configuration.

        Args:
            content: Configuration content

        Returns:
            Dictionary with configuration statistics
        """
        lines = content.split('\n')
        stats = {
            'Total commands': len(lines),
            'Set commands': 0,
            'Delete commands': 0,
            'Other commands': 0,
            'Unique hierarchies': set()
        }

        for line in lines:
            if not line.strip():
                continue

            parts = line.split()
            command = parts[0].lower()

            if command == 'set':
                stats['Set commands'] += 1
                if len(parts) > 1:
                    hierarchy = parts[1].split('.')[0]
                    stats['Unique hierarchies'].add(hierarchy)
            elif command == 'delete':
                stats['Delete commands'] += 1
            else:
                stats['Other commands'] += 1

        stats['Unique hierarchies'] = len(stats['Unique hierarchies'])
        return stats
