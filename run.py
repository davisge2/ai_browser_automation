#!/usr/bin/env python3
"""
Desktop Automation Recorder
===========================

A production-grade desktop automation tool that records user actions
and replays them on schedule with email notifications.

Usage:
    python run.py           # Start the GUI application
    python run.py --help    # Show help
    python run.py --cli     # CLI mode (for headless servers)
"""
import sys
import os
import argparse
import logging
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))


def setup_logging(verbose: bool = False):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(Path.home() / ".desktop-automation" / "app.log")
        ]
    )


def check_dependencies():
    """Check if all dependencies are installed."""
    missing = []
    
    try:
        import PyQt6
    except ImportError:
        missing.append("PyQt6")
    
    try:
        import pynput
    except ImportError:
        missing.append("pynput")
    
    try:
        import pyautogui
    except ImportError:
        missing.append("pyautogui")
    
    try:
        import mss
    except ImportError:
        missing.append("mss")
    
    try:
        import apscheduler
    except ImportError:
        missing.append("apscheduler")
    
    if missing:
        print("Missing dependencies:")
        for dep in missing:
            print(f"  - {dep}")
        print("\nInstall with: pip install -r requirements.txt")
        sys.exit(1)


def run_gui():
    """Run the GUI application."""
    from PyQt6.QtWidgets import QApplication
    from gui import MainWindow
    
    app = QApplication(sys.argv)
    app.setApplicationName("Desktop Automation Recorder")
    app.setOrganizationName("Automation")
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


def run_cli():
    """Run in CLI mode."""
    print("CLI mode not implemented yet.")
    print("Use the GUI: python run.py")


def main():
    parser = argparse.ArgumentParser(
        description="Desktop Automation Recorder - Record and replay browser workflows"
    )
    parser.add_argument(
        "--cli", 
        action="store_true",
        help="Run in CLI mode (no GUI)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check dependencies and exit"
    )
    
    args = parser.parse_args()
    
    # Create app directory
    app_dir = Path.home() / ".desktop-automation"
    app_dir.mkdir(exist_ok=True)
    
    setup_logging(args.verbose)
    check_dependencies()
    
    if args.check:
        print("All dependencies installed!")
        sys.exit(0)
    
    if args.cli:
        run_cli()
    else:
        run_gui()


if __name__ == "__main__":
    main()
