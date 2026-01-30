"""
Desktop Action Playback Engine
Replays recorded actions with error handling and visual verification.

Features:
- Replays all recorded action types
- Visual verification before clicks
- Retry logic for failed actions
- Credential injection from secure storage
- Speed control
- Pause/resume support
"""
import os
import time
import logging
import threading
import webbrowser
import subprocess
import platform
import winreg
from pathlib import Path
from typing import Optional, List, Callable, Dict, Any
from dataclasses import dataclass
from enum import Enum

import pyautogui
from PIL import Image

from recorder import RecordedAction, ActionType, Recording, ScreenRegion, ScreenCapture
from credentials import CredentialManager, SecureInput
from page_monitor import PageLoadTimer

logger = logging.getLogger(__name__)

# PyAutoGUI safety settings
pyautogui.FAILSAFE = True  # Move mouse to corner to abort
pyautogui.PAUSE = 0.1  # Small pause between actions


class PlaybackStatus(str, Enum):
    """Playback execution status."""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


@dataclass
class PlaybackResult:
    """Result of a single action playback."""
    success: bool
    action_id: str
    action_type: ActionType
    error_message: Optional[str] = None
    screenshot_path: Optional[str] = None
    retry_count: int = 0
    duration_ms: int = 0
    page_load_time_ms: Optional[int] = None


@dataclass
class PlaybackReport:
    """Complete playback execution report."""
    recording_id: str
    recording_name: str
    status: PlaybackStatus
    started_at: float
    completed_at: Optional[float]
    total_actions: int
    completed_actions: int
    failed_actions: int
    screenshots: List[str]
    results: List[PlaybackResult]
    error_message: Optional[str] = None
    
    @property
    def duration_seconds(self) -> float:
        if self.completed_at:
            return self.completed_at - self.started_at
        return time.time() - self.started_at
    
    @property
    def success_rate(self) -> float:
        if self.total_actions == 0:
            return 0.0
        return (self.completed_actions / self.total_actions) * 100


class VisualVerifier:
    """Verifies screen state before executing actions."""
    
    def __init__(self, screen_capture: ScreenCapture, threshold: float = 0.85):
        self.screen_capture = screen_capture
        self.threshold = threshold
    
    def verify_location(
        self, 
        x: int, 
        y: int, 
        expected_region: Optional[ScreenRegion],
        timeout: float = 10.0
    ) -> bool:
        """
        Verify that the expected visual is present at location.
        
        Returns True if:
        - No expected region provided (skip verification)
        - Visual match found within timeout
        """
        if not expected_region or not expected_region.image_path:
            return True
        
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            current_region = self.screen_capture.capture_region(
                x, y, 
                expected_region.width, 
                expected_region.height,
                save=False
            )
            
            if self.screen_capture.compare_regions(
                expected_region, current_region, self.threshold
            ):
                return True
            
            time.sleep(0.5)
        
        logger.warning(f"Visual verification failed at ({x}, {y})")
        return False
    
    def find_on_screen(
        self, 
        expected_region: ScreenRegion,
        confidence: float = 0.8
    ) -> Optional[tuple]:
        """
        Find the expected visual anywhere on screen.
        
        Returns (x, y) center coordinates if found, None otherwise.
        """
        if not expected_region.image_path:
            return None
        
        try:
            location = pyautogui.locateOnScreen(
                expected_region.image_path,
                confidence=confidence
            )
            
            if location:
                center = pyautogui.center(location)
                return (center.x, center.y)
                
        except Exception as e:
            logger.debug(f"Image search failed: {e}")
        
        return None


class PlaybackEngine:
    """
    Executes recorded actions with reliability features.
    
    Features:
    - Visual verification before clicks
    - Automatic retry on failure
    - Credential injection
    - Speed control
    - Comprehensive error handling
    - Screenshot capture at key points
    """
    
    def __init__(
        self,
        credential_manager: CredentialManager,
        storage_dir: Path,
        on_action_start: Optional[Callable[[RecordedAction], None]] = None,
        on_action_complete: Optional[Callable[[PlaybackResult], None]] = None,
        on_status_change: Optional[Callable[[PlaybackStatus], None]] = None
    ):
        self.credential_manager = credential_manager
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        
        self.on_action_start = on_action_start
        self.on_action_complete = on_action_complete
        self.on_status_change = on_status_change
        
        self._screen_capture = ScreenCapture(storage_dir / "playback_screenshots")
        self._verifier = VisualVerifier(self._screen_capture)
        self._step_screenshots: Dict[str, str] = {}
        self._page_load_timer = PageLoadTimer()
        self.screenshot_output_dir: Optional[Path] = None

        self._status = PlaybackStatus.IDLE
        self._current_recording: Optional[Recording] = None
        self._current_action_index = 0
        self._abort_requested = False
        self._pause_event = threading.Event()
        self._pause_event.set()  # Not paused initially
        
        # Playback settings
        self.speed_multiplier = 1.0
        self.verify_visuals = True
        self.max_retries = 3
        self.retry_delay = 1.0
        self.action_timeout = 30.0
    
    def _set_status(self, status: PlaybackStatus) -> None:
        """Update status and notify listeners."""
        self._status = status
        if self.on_status_change:
            self.on_status_change(status)
    
    def execute(
        self,
        recording: Recording,
        speed_multiplier: float = 1.0,
        verify_visuals: bool = False
    ) -> PlaybackReport:
        """
        Execute a complete recording.
        
        Args:
            recording: The recording to execute
            speed_multiplier: Speed adjustment (1.0 = normal, 2.0 = 2x faster)
            verify_visuals: Whether to verify screenshots before clicks
        
        Returns:
            PlaybackReport with execution details
        """
        self._current_recording = recording
        self.speed_multiplier = speed_multiplier
        self.verify_visuals = verify_visuals
        self._abort_requested = False
        self._current_action_index = 0
        self._step_screenshots = {}
        
        report = PlaybackReport(
            recording_id=recording.id,
            recording_name=recording.name,
            status=PlaybackStatus.RUNNING,
            started_at=time.time(),
            completed_at=None,
            total_actions=len(recording.actions),
            completed_actions=0,
            failed_actions=0,
            screenshots=[],
            results=[]
        )
        
        self._set_status(PlaybackStatus.RUNNING)
        logger.info(f"Starting playback: {recording.name} ({len(recording.actions)} actions)")
        
        try:
            # Open starting URL if specified
            if recording.url:
                self._open_url(recording.url)
                time.sleep(2)  # Wait for browser to load
            
            # Execute each action
            for idx, action in enumerate(recording.actions):
                # Check for abort
                if self._abort_requested:
                    report.status = PlaybackStatus.ABORTED
                    report.error_message = "Playback aborted by user"
                    break
                
                # Wait if paused
                self._pause_event.wait()
                
                self._current_action_index = idx
                
                # Notify action start
                if self.on_action_start:
                    self.on_action_start(action)
                
                # Execute with retry
                result = self._execute_action_with_retry(action)
                report.results.append(result)
                
                if result.success:
                    report.completed_actions += 1
                else:
                    report.failed_actions += 1
                    
                    # Check if we should abort on failure (retry_on_failure=0 means no retries)
                    if recording.retry_on_failure <= 0:
                        report.status = PlaybackStatus.FAILED
                        report.error_message = f"Action failed: {result.error_message}"
                        break
                
                # Collect screenshots
                if result.screenshot_path:
                    report.screenshots.append(result.screenshot_path)
                
                # Notify action complete
                if self.on_action_complete:
                    self.on_action_complete(result)
            
            # Set final status
            if report.status == PlaybackStatus.RUNNING:
                if report.failed_actions == 0:
                    report.status = PlaybackStatus.COMPLETED
                else:
                    report.status = PlaybackStatus.COMPLETED  # Completed with errors
            
        except Exception as e:
            logger.error(f"Playback failed: {e}")
            report.status = PlaybackStatus.FAILED
            report.error_message = str(e)
        
        finally:
            report.completed_at = time.time()
            self._set_status(report.status)
            self._current_recording = None
        
        logger.info(
            f"Playback complete: {report.completed_actions}/{report.total_actions} "
            f"({report.success_rate:.1f}% success)"
        )
        
        return report
    
    def _execute_action_with_retry(self, action: RecordedAction) -> PlaybackResult:
        """Execute action with retry logic."""
        start_time = time.time()
        last_error = None
        
        for attempt in range(self.max_retries + 1):
            try:
                # Apply delay (adjusted for speed)
                if action.delay_before > 0:
                    adjusted_delay = action.delay_before / self.speed_multiplier
                    time.sleep(min(adjusted_delay, 5.0))  # Cap at 5 seconds
                
                # Execute action
                screenshot_path = self._execute_action(action)

                # Measure page load time for URL/click actions
                page_load_ms = None
                if action.action_type in (ActionType.OPEN_URL, ActionType.MOUSE_CLICK):
                    try:
                        page_load_ms = self._page_load_timer.measure_load_time(timeout_ms=10000)
                    except Exception as e:
                        logger.debug(f"Page load timing failed: {e}")

                # Capture step screenshot only for explicit screenshot actions
                if action.action_type == ActionType.SCREENSHOT:
                    try:
                        step_ss = self._screen_capture.capture_full_screen()
                        self._step_screenshots[action.id] = step_ss
                    except Exception as e:
                        logger.debug(f"Step screenshot failed: {e}")

                duration_ms = int((time.time() - start_time) * 1000)

                return PlaybackResult(
                    success=True,
                    action_id=action.id,
                    action_type=action.action_type,
                    screenshot_path=screenshot_path,
                    retry_count=attempt,
                    duration_ms=duration_ms,
                    page_load_time_ms=page_load_ms,
                )
                
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Action {action.id} failed (attempt {attempt + 1}): {e}")
                
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)
        
        duration_ms = int((time.time() - start_time) * 1000)
        
        return PlaybackResult(
            success=False,
            action_id=action.id,
            action_type=action.action_type,
            error_message=last_error,
            retry_count=self.max_retries,
            duration_ms=duration_ms
        )
    
    def _execute_action(self, action: RecordedAction) -> Optional[str]:
        """Execute a single action."""
        logger.debug(f"Executing: {action.action_type.value} - {action.description}")
        
        at = action.action_type
        if at == ActionType.MOUSE_CLICK:
            return self._execute_click(action)
        elif at == ActionType.MOUSE_DOUBLE_CLICK:
            return self._execute_double_click(action)
        elif at == ActionType.MOUSE_RIGHT_CLICK:
            return self._execute_right_click(action)
        elif at == ActionType.MOUSE_SCROLL:
            return self._execute_scroll(action)
        elif at == ActionType.KEY_PRESS:
            return self._execute_key_press(action)
        elif at == ActionType.KEY_TYPE:
            return self._execute_type(action)
        elif at == ActionType.CREDENTIAL_INPUT:
            return self._execute_credential_input(action)
        elif at == ActionType.SCREENSHOT:
            return self._execute_screenshot(action)
        elif at == ActionType.WAIT:
            time.sleep(action.delay_before)
            return None
        elif at == ActionType.OPEN_URL:
            self._open_url(action.text)
            return None
        elif at == ActionType.HOTKEY:
            return self._execute_hotkey(action)
        else:
            logger.warning(f"Unknown action type: {action.action_type}")
            return None
    
    def _execute_click(self, action: RecordedAction) -> Optional[str]:
        """Execute mouse click with visual verification."""
        x, y = action.x, action.y
        
        # Visual verification
        if self.verify_visuals and action.screen_region:
            if not self._verifier.verify_location(x, y, action.screen_region):
                # Try to find the element on screen
                new_location = self._verifier.find_on_screen(action.screen_region)
                if new_location:
                    x, y = new_location
                    logger.info(f"Element found at new location: ({x}, {y})")
                else:
                    raise Exception(f"Visual verification failed at ({x}, {y})")
        
        pyautogui.click(x, y)
        return None
    
    def _execute_double_click(self, action: RecordedAction) -> Optional[str]:
        """Execute double click."""
        x, y = action.x, action.y
        
        if self.verify_visuals and action.screen_region:
            if not self._verifier.verify_location(x, y, action.screen_region):
                new_location = self._verifier.find_on_screen(action.screen_region)
                if new_location:
                    x, y = new_location
                else:
                    raise Exception(f"Visual verification failed at ({x}, {y})")
        
        pyautogui.doubleClick(x, y)
        return None
    
    def _execute_right_click(self, action: RecordedAction) -> Optional[str]:
        """Execute right click."""
        x, y = action.x, action.y
        
        if self.verify_visuals and action.screen_region:
            if not self._verifier.verify_location(x, y, action.screen_region):
                new_location = self._verifier.find_on_screen(action.screen_region)
                if new_location:
                    x, y = new_location
                else:
                    raise Exception(f"Visual verification failed at ({x}, {y})")
        
        pyautogui.rightClick(x, y)
        return None
    
    def _execute_scroll(self, action: RecordedAction) -> Optional[str]:
        """Execute scroll action."""
        if action.x is not None and action.y is not None:
            pyautogui.moveTo(action.x, action.y)
        
        # PyAutoGUI scroll uses clicks, dy is typically -1 or 1
        clicks = action.dy if action.dy else 0
        pyautogui.scroll(clicks)
        return None
    
    def _execute_key_press(self, action: RecordedAction) -> Optional[str]:
        """Execute single key press."""
        key = action.key
        
        # Map common key names
        key_map = {
            'enter': 'return',
            'return': 'return',
            'esc': 'escape',
            'del': 'delete',
        }
        
        key = key_map.get(key.lower(), key)
        pyautogui.press(key)
        return None
    
    def _execute_type(self, action: RecordedAction) -> Optional[str]:
        """Execute text typing."""
        if not action.text:
            return None
        
        # Use write() instead of typewrite() to support non-ASCII characters
        pyautogui.write(action.text)
        return None
    
    def _execute_credential_input(self, action: RecordedAction) -> Optional[str]:
        """
        Execute credential input securely.
        
        Fetches the actual credential from secure storage and types it.
        The credential value is never logged or stored in the recording.
        """
        if not action.credential_name or not action.credential_field:
            raise Exception("Invalid credential action: missing name or field")
        
        credential = self.credential_manager.get_credential(action.credential_name)
        if not credential:
            raise Exception(f"Credential not found: {action.credential_name}")
        
        # Get the appropriate field value
        if action.credential_field == "password":
            value = credential.password
        elif action.credential_field == "username":
            value = credential.username
        else:
            raise Exception(f"Unknown credential field: {action.credential_field}")
        
        # Type the credential value
        # Using write() instead of typewrite() for special characters
        pyautogui.write(value)
        
        # Clear from memory
        credential.clear()
        
        return None
    
    def _execute_screenshot(self, action: RecordedAction) -> Optional[str]:
        """Take a full-screen screenshot and save to the configured output folder."""
        import mss
        import mss.tools
        from datetime import datetime as dt

        out_dir = self.screenshot_output_dir or self.storage_dir / "playback_screenshots"
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        timestamp = dt.now().strftime("%Y%m%d_%H%M%S_%f")
        filepath = str(out_dir / f"screenshot_{timestamp}.png")

        with mss.mss() as sct:
            screenshot = sct.grab(sct.monitors[0])
            mss.tools.to_png(screenshot.rgb, screenshot.size, output=filepath)

        logger.info(f"Screenshot saved: {filepath}")
        return filepath
    
    def _execute_hotkey(self, action: RecordedAction) -> Optional[str]:
        """Execute hotkey combination."""
        keys = action.text.split('+') if action.text else []
        if keys:
            pyautogui.hotkey(*keys)
        return None
    
    def _open_url(self, url: str) -> None:
        """Open URL in Chrome (falls back to default browser)."""
        chrome_path = self._find_chrome()
        if chrome_path:
            subprocess.Popen([chrome_path, url])
        else:
            webbrowser.open(url)
        time.sleep(2)  # Wait for browser

    @staticmethod
    def _find_chrome() -> Optional[str]:
        """Locate Chrome executable on Windows."""
        common_paths = [
            Path(os.environ.get("PROGRAMFILES", "C:\\Program Files")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        ]
        for p in common_paths:
            if p.exists():
                return str(p)
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe")
            path, _ = winreg.QueryValueEx(key, "")
            winreg.CloseKey(key)
            if Path(path).exists():
                return path
        except OSError:
            pass
        return None
    
    def pause(self) -> None:
        """Pause playback."""
        self._pause_event.clear()
        self._set_status(PlaybackStatus.PAUSED)
        logger.info("Playback paused")
    
    def resume(self) -> None:
        """Resume playback."""
        self._pause_event.set()
        self._set_status(PlaybackStatus.RUNNING)
        logger.info("Playback resumed")
    
    def abort(self) -> None:
        """Abort playback."""
        self._abort_requested = True
        self._pause_event.set()  # Unblock if paused
        logger.info("Playback abort requested")
    
    @property
    def step_screenshots(self) -> Dict[str, str]:
        """Map of action_id to screenshot path captured during playback."""
        return self._step_screenshots.copy()

    @property
    def status(self) -> PlaybackStatus:
        return self._status
    
    @property
    def current_action_index(self) -> int:
        return self._current_action_index
    
    @property
    def current_recording(self) -> Optional[Recording]:
        return self._current_recording
    
    def close(self) -> None:
        """Clean up resources."""
        self._screen_capture.close()
