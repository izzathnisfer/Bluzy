#!/usr/bin/env python3
# Bluzy — Entry Point
# Copyright (c) 2026 Bluzy Contributors
# SPDX-License-Identifier: MIT

"""
Bluzy application entry point.

Initializes the D-Bus GLib main loop integration, parses command-line
arguments, configures logging, and starts the GTK4 application.

Usage:
    python3 main.py                 # Start normally
    python3 main.py --minimized     # Start minimized to system tray
    python3 main.py --version       # Print version and exit
    python3 main.py --debug         # Enable debug logging
"""

import argparse
import logging
import os
import signal
import sys

# Ensure the project root is on sys.path so 'from bluzy import ...' works
# when invoked as `python3 bluzy/main.py` from the project directory.
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# D-Bus main loop must be set BEFORE importing gi or creating any D-Bus objects
import dbus.mainloop.glib
dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

from bluzy import __app_name__, __version__
from bluzy.app import BluzyApp


def setup_logging(debug: bool = False) -> None:
    """Configure application-wide logging."""
    level = logging.DEBUG if debug else logging.INFO
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger("bluzy")
    root.setLevel(level)
    root.addHandler(handler)

    # Silence noisy D-Bus debug messages unless in debug mode
    if not debug:
        logging.getLogger("dbus").setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="bluzy",
        description=(
            f"{__app_name__} — Turn your laptop into a Bluetooth speaker"
        ),
    )
    parser.add_argument(
        "--minimized",
        action="store_true",
        help="Start minimized in the system tray",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{__app_name__} {__version__}",
    )
    return parser.parse_args()


def main() -> int:
    """Application entry point."""
    args = parse_args()
    setup_logging(debug=args.debug)

    logger = logging.getLogger(__name__)
    logger.info(
        "Starting %s v%s (minimized=%s, debug=%s)",
        __app_name__,
        __version__,
        args.minimized,
        args.debug,
    )

    # Allow Ctrl+C to terminate the app cleanly
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)

    app = BluzyApp(start_minimized=args.minimized)
    return app.run(sys.argv[:1])  # Pass only prog name; args already parsed


if __name__ == "__main__":
    sys.exit(main())
