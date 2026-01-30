"""
Desktop Action Recorder
Records all user interactions for later playback.

Captures:
- Mouse clicks, double-clicks, right-clicks
- Mouse movements and drags
- Keyboard input (with sensitive field masking)
- Screenshots of clicked areas for visual verification
- Window focus changes
- Scroll events
"""
import time
import json
import logging
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass, field, fields, asdict
from enum import Enum
import mss
import mss.tools
from PIL import Image
import io

logger = logging.getLogger(__name__)


class ActionType(str, Enum):
    """Types of recordable actions."""
    MOUSE_CLICK = "mouse_click"
    MOUSE_DOUBLE_CLICK = "mouse_double_click"
    MOUSE_RIGHT_CLICK = "mouse_right_click"
    MOUSE_MOVE = "mouse_move"
    MOUSE_DRAG = "mouse_drag"
    MOUSE_SCROLL = "mouse_scroll"
    KEY_PRESS = "key_press"
    KEY_RELEASE = "key_release"
    KEY_TYPE = "key_type"  # Complete text input
    HOTKEY = "hotkey"
    SCREENSHOT = "screenshot"
    WAIT = "wait"
    CREDENTIAL_INPUT = "credential_input"  # Secure credential reference
    WINDOW_FOCUS = "window_focus"
    OPEN_APPLICATION = "open_application"
    OPEN_URL = "open_url"


@dataclass
class ScreenRegion:
    """Captured screen region for visual verification."""
    x: int
    y: int
    width: int
    height: int
    image_path: Optional[str] = None
    image_hash: Optional[str] = None


@dataclass
class RecordedAction:
    """Single recorded action with all metadata."""
    id: str
    action_type: ActionType
    timestamp: float
    
    # Position data
    x: Optional[int] = None
    y: Optional[int] = None
    
    # Input data
    key: Optional[str] = None
    text: Optional[str] = None
    button: Optional[str] = None
    
    # Scroll data
    dx: Optional[int] = None
    dy: Optional[int] = None
    
    # Credential reference (never stores actual values)
    credential_name: Optional[str] = None
    credential_field: Optional[str] = None  # "username" or "password"
    
    # Visual verification
    screen_region: Optional[ScreenRegion] = None
    
    # Timing
    delay_before: float = 0.0  # Delay from previous action
    
    # Window context
    window_title: Optional[str] = None
    window_class: Optional[str] = None
    
    # Additional options
    options: Dict[str, Any] = field(default_factory=dict)
    
    # Human-readable description
    description: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = {
            "id": self.id,
            "action_type": self.action_type.value,
            "timestamp": self.timestamp,
            "x": self.x,
            "y": self.y,
            "key": self.key,
            "text": self.text,
            "button": self.button,
            "dx": self.dx,
            "dy": self.dy,
            "credential_name": self.credential_name,
            "credential_field": self.credential_field,
            "delay_before": self.delay_before,
            "window_title": self.window_title,
            "window_class": self.window_class,
            "options": self.options,
            "description": self.description,
        }
        
        if self.screen_region:
            data["screen_region"] = asdict(self.screen_region)
        
        return {k: v for k, v in data.items() if v is not None}
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RecordedAction":
        """Create from dictionary."""
        data = data.copy()
        screen_region = None
        if "screen_region" in data:
            screen_region = ScreenRegion(**data.pop("screen_region"))

        data["action_type"] = ActionType(data["action_type"])

        # Filter to only known fields to avoid TypeError on unexpected keys
        valid_fields = {f.name for f in fields(cls) if f.name != "screen_region"}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered, screen_region=screen_region)


@dataclass
class Recording:
    """Complete recording with metadata."""
    id: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    url: Optional[str]  # Starting URL if browser-based
    actions: List[RecordedAction]
    
    # Recording settings
    capture_screenshots: bool = True
    screenshot_region_size: int = 100  # Pixels around click point
    
    # Playback settings
    speed_multiplier: float = 1.0
    verify_screenshots: bool = True
    retry_on_failure: int = 3

    # Audit metadata
    audit_purpose: Optional[str] = None
    audit_verification_goal: Optional[str] = None
    step_screenshot_paths: Dict[str, str] = field(default_factory=dict)
    email_recipients: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "url": self.url,
            "actions": [a.to_dict() for a in self.actions],
            "capture_screenshots": self.capture_screenshots,
            "screenshot_region_size": self.screenshot_region_size,
            "speed_multiplier": self.speed_multiplier,
            "verify_screenshots": self.verify_screenshots,
            "retry_on_failure": self.retry_on_failure,
            "audit_purpose": self.audit_purpose,
            "audit_verification_goal": self.audit_verification_goal,
            "step_screenshot_paths": self.step_screenshot_paths,
            "email_recipients": self.email_recipients,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Recording":
        data = data.copy()
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        data["actions"] = [RecordedAction.from_dict(a) for a in data["actions"]]
        # Handle fields that may not exist in older recordings
        data.setdefault("audit_purpose", None)
        data.setdefault("audit_verification_goal", None)
        data.setdefault("step_screenshot_paths", {})
        data.setdefault("email_recipients", [])
        # Filter to only known fields to avoid TypeError on unexpected keys
        valid_fields = {f.name for f in fields(cls)}
        data = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**data)
    
    def save(self, path: Path) -> None:
        """Save recording to file."""
        path.write_text(json.dumps(self.to_dict(), indent=2))
    
    @classmethod
    def load(cls, path: Path) -> "Recording":
        """Load recording from file."""
        return cls.from_dict(json.loads(path.read_text()))


class ScreenCapture:
    """Efficient screen capture utility."""
    
    def __init__(self, storage_dir: Path):
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()

    def _get_sct(self):
        """Get a thread-local mss instance."""
        if not hasattr(self._local, 'sct'):
            self._local.sct = mss.mss()
        return self._local.sct

    def capture_region(
        self,
        x: int,
        y: int,
        width: int = 100,
        height: int = 100,
        save: bool = True
    ) -> ScreenRegion:
        """Capture a region around a point."""
        # Center the region on the point
        left = max(0, x - width // 2)
        top = max(0, y - height // 2)

        monitor = {
            "left": left,
            "top": top,
            "width": width,
            "height": height
        }

        screenshot = self._get_sct().grab(monitor)
        
        image_path = None
        image_hash = None
        
        if save:
            # Save screenshot
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            filename = f"region_{timestamp}.png"
            image_path = str(self.storage_dir / filename)
            mss.tools.to_png(screenshot.rgb, screenshot.size, output=image_path)
            
            # Calculate hash for comparison
            import hashlib
            image_hash = hashlib.md5(screenshot.rgb).hexdigest()
        
        return ScreenRegion(
            x=left,
            y=top,
            width=width,
            height=height,
            image_path=image_path,
            image_hash=image_hash
        )
    
    def capture_full_screen(self) -> str:
        """Capture full screen screenshot."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"screenshot_{timestamp}.png"
        filepath = str(self.storage_dir / filename)
        
        sct = self._get_sct()
        screenshot = sct.grab(sct.monitors[0])
        mss.tools.to_png(screenshot.rgb, screenshot.size, output=filepath)
        
        return filepath
    
    def compare_regions(self, region1: ScreenRegion, region2: ScreenRegion, threshold: float = 0.95) -> bool:
        """Compare two screen regions for similarity."""
        if not region1.image_path or not region2.image_path:
            return False
        
        try:
            import cv2
            import numpy as np
            
            img1 = cv2.imread(region1.image_path)
            img2 = cv2.imread(region2.image_path)
            
            if img1 is None or img2 is None:
                return False
            
            # Resize to same dimensions if needed
            if img1.shape != img2.shape:
                img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))
            
            # Calculate structural similarity
            gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
            gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)
            
            # Simple correlation comparison
            result = cv2.matchTemplate(gray1, gray2, cv2.TM_CCOEFF_NORMED)
            similarity = result[0][0]
            
            return similarity >= threshold
            
        except ImportError:
            # Fall back to hash comparison
            return region1.image_hash == region2.image_hash
    
    def close(self):
        """Clean up resources."""
        if hasattr(self._local, 'sct'):
            self._local.sct.close()


class ActionRecorder:
    """
    Records user actions on the desktop.
    
    Features:
    - Records mouse clicks with visual context
    - Records keyboard input with sensitive field masking
    - Captures timing for natural playback
    - Supports credential references instead of actual values
    """
    
    def __init__(
        self, 
        storage_dir: Path,
        on_action: Optional[Callable[[RecordedAction], None]] = None
    ):
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.on_action = on_action
        
        self._screen_capture = ScreenCapture(storage_dir / "screenshots")
        self._actions: List[RecordedAction] = []
        self._step_screenshots: Dict[str, str] = {}
        self._recording = False
        self._paused = False
        self._last_action_time: Optional[float] = None
        self._action_counter = 0
        self._lock = threading.Lock()
        
        # Rect to exclude from click recording (toolbar area)
        self._exclude_rect = None  # tuple (x, y, w, h) or None

        # Text accumulator for key_type actions
        self._text_buffer = ""
        self._text_buffer_start_time: Optional[float] = None
        self._text_flush_delay = 0.5  # Seconds of inactivity before flushing

        # Scroll accumulator for debouncing
        self._scroll_buffer_x = 0
        self._scroll_buffer_y = 0
        self._scroll_last_x: Optional[int] = None
        self._scroll_last_y: Optional[int] = None
        self._scroll_start_time: Optional[float] = None
        self._scroll_timer: Optional[threading.Timer] = None
        
        # Sensitive field tracking
        self._sensitive_mode = False
        self._current_credential: Optional[tuple] = None  # (name, field)
        
        # Mouse/keyboard listeners
        self._mouse_listener = None
        self._keyboard_listener = None

        # Win32 low-level scroll hook (pynput often misses scroll on Windows)
        self._scroll_hook = None
        self._scroll_hook_thread = None
        self._scroll_hook_proc_ref = None  # prevent GC of the callback
    
    def _generate_action_id(self) -> str:
        """Generate unique action ID."""
        self._action_counter += 1
        return f"action_{self._action_counter:05d}"
    
    def _get_delay(self) -> float:
        """Calculate delay from last action."""
        now = time.time()
        if self._last_action_time is None:
            delay = 0.0
        else:
            delay = now - self._last_action_time
        self._last_action_time = now
        return delay
    
    def _get_window_info(self) -> tuple:
        """Get current window title and class."""
        try:
            import platform
            if platform.system() == "Windows":
                import ctypes
                from ctypes import wintypes
                
                user32 = ctypes.windll.user32
                hwnd = user32.GetForegroundWindow()
                
                length = user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                
                return buf.value, None
            elif platform.system() == "Darwin":
                # macOS
                from AppKit import NSWorkspace
                active_app = NSWorkspace.sharedWorkspace().frontmostApplication()
                return active_app.localizedName(), None
            else:
                # Linux - try xdotool
                import subprocess
                result = subprocess.run(
                    ["xdotool", "getactivewindow", "getwindowname"],
                    capture_output=True, text=True
                )
                return result.stdout.strip(), None
        except Exception as e:
            logger.debug(f"Could not get window info: {e}")
            return None, None
    
    @property
    def step_screenshots(self) -> Dict[str, str]:
        """Map of action_id to screenshot path for each recorded step."""
        return self._step_screenshots.copy()

    def _record_action(self, action: RecordedAction) -> None:
        """Add action to recording."""
        if not self._recording or self._paused:
            return

        with self._lock:
            self._actions.append(action)

        if self.on_action:
            self.on_action(action)

        logger.debug(f"Recorded: {action.action_type.value} - {action.description}")
    
    def _flush_text_buffer(self) -> None:
        """Flush accumulated text as a single action."""
        if not self._text_buffer:
            return
        
        if self._sensitive_mode and self._current_credential:
            # Record as credential reference, not actual text
            action = RecordedAction(
                id=self._generate_action_id(),
                action_type=ActionType.CREDENTIAL_INPUT,
                timestamp=self._text_buffer_start_time or time.time(),
                credential_name=self._current_credential[0],
                credential_field=self._current_credential[1],
                delay_before=self._get_delay(),
                description=f"Enter {self._current_credential[1]} for {self._current_credential[0]}"
            )
            # Auto-reset sensitive mode after capturing the credential input
            self._sensitive_mode = False
            self._current_credential = None
        else:
            action = RecordedAction(
                id=self._generate_action_id(),
                action_type=ActionType.KEY_TYPE,
                timestamp=self._text_buffer_start_time or time.time(),
                text=self._text_buffer,
                delay_before=self._get_delay(),
                description=f"Type: {self._text_buffer[:30]}{'...' if len(self._text_buffer) > 30 else ''}"
            )
        
        self._record_action(action)
        self._text_buffer = ""
        self._text_buffer_start_time = None
    
    def start_recording(self, capture_screenshots: bool = True) -> None:
        """Start recording user actions."""
        if self._recording:
            return
        
        self._recording = True
        self._paused = False
        self._actions = []
        self._last_action_time = time.time()
        self._capture_screenshots = capture_screenshots
        
        # Import here to avoid issues if not installed
        from pynput import mouse, keyboard
        
        # Mouse listener (scroll handled by Win32 hook on Windows for reliability)
        import platform
        scroll_handler = None if platform.system() == "Windows" else self._on_mouse_scroll
        self._mouse_listener = mouse.Listener(
            on_click=self._on_mouse_click,
            on_scroll=scroll_handler
        )
        
        # Keyboard listener
        self._keyboard_listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release
        )
        
        self._mouse_listener.start()
        self._keyboard_listener.start()

        # Start Win32 low-level scroll hook for reliable scroll capture
        self._start_win32_scroll_hook()

        logger.info("Recording started")
    
    def pause_recording(self) -> None:
        """Pause recording."""
        self._paused = True
        self._flush_text_buffer()
        logger.info("Recording paused")
    
    def resume_recording(self) -> None:
        """Resume recording."""
        self._paused = False
        self._last_action_time = time.time()
        logger.info("Recording resumed")
    
    def stop_recording(self) -> List[RecordedAction]:
        """Stop recording and return actions."""
        self._flush_text_buffer()
        if self._scroll_timer is not None:
            self._scroll_timer.cancel()
        self._flush_scroll_buffer()
        self._recording = False
        
        if self._mouse_listener:
            self._mouse_listener.stop()
        if self._keyboard_listener:
            self._keyboard_listener.stop()
        self._stop_win32_scroll_hook()

        logger.info(f"Recording stopped. {len(self._actions)} actions recorded.")
        return self._actions.copy()
    
    def mark_sensitive_input(self, credential_name: str, field: str) -> None:
        """
        Mark the next input as sensitive (credential).
        
        Instead of recording actual keystrokes, we'll record a reference
        to the credential that will be fetched during playback.
        """
        self._flush_text_buffer()
        self._sensitive_mode = True
        self._current_credential = (credential_name, field)
        logger.info(f"Sensitive input mode: {credential_name}/{field}")
    
    def end_sensitive_input(self) -> None:
        """End sensitive input mode."""
        self._flush_text_buffer()
        self._sensitive_mode = False
        self._current_credential = None
        logger.info("Sensitive input mode ended")
    
    def add_screenshot_action(self) -> str:
        """Manually add a screenshot action."""
        filepath = self._screen_capture.capture_full_screen()
        
        action = RecordedAction(
            id=self._generate_action_id(),
            action_type=ActionType.SCREENSHOT,
            timestamp=time.time(),
            delay_before=self._get_delay(),
            options={"filepath": filepath},
            description="Take screenshot"
        )
        self._record_action(action)
        return filepath
    
    def add_wait_action(self, seconds: float) -> None:
        """Add explicit wait action."""
        action = RecordedAction(
            id=self._generate_action_id(),
            action_type=ActionType.WAIT,
            timestamp=time.time(),
            delay_before=seconds,
            description=f"Wait {seconds} seconds"
        )
        self._record_action(action)
    
    def add_url_action(self, url: str) -> None:
        """Add open URL action."""
        action = RecordedAction(
            id=self._generate_action_id(),
            action_type=ActionType.OPEN_URL,
            timestamp=time.time(),
            text=url,
            delay_before=self._get_delay(),
            description=f"Open URL: {url}"
        )
        self._record_action(action)
    
    # ===== Event Handlers =====
    
    def _on_mouse_click(self, x: int, y: int, button, pressed: bool) -> None:
        """Handle mouse click events."""
        if not pressed:  # Only record on press, not release
            return

        # Skip clicks inside the excluded rect (floating toolbar)
        if self._exclude_rect:
            rx, ry, rw, rh = self._exclude_rect
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return

        self._flush_text_buffer()
        
        # Determine click type
        button_name = button.name if hasattr(button, 'name') else str(button)
        
        if button_name == "left":
            action_type = ActionType.MOUSE_CLICK
        elif button_name == "right":
            action_type = ActionType.MOUSE_RIGHT_CLICK
        else:
            action_type = ActionType.MOUSE_CLICK
        
        # Capture screen region around click
        screen_region = None
        if self._capture_screenshots:
            screen_region = self._screen_capture.capture_region(x, y, 100, 100)
        
        window_title, window_class = self._get_window_info()
        
        action = RecordedAction(
            id=self._generate_action_id(),
            action_type=action_type,
            timestamp=time.time(),
            x=x,
            y=y,
            button=button_name,
            screen_region=screen_region,
            delay_before=self._get_delay(),
            window_title=window_title,
            window_class=window_class,
            description=f"{button_name.title()} click at ({x}, {y})"
        )
        self._record_action(action)
    
    def _on_mouse_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        """Handle mouse scroll events with debouncing."""
        if not self._recording or self._paused:
            return

        # Cancel pending flush timer
        if self._scroll_timer is not None:
            self._scroll_timer.cancel()

        # Start accumulating
        if self._scroll_start_time is None:
            self._scroll_start_time = time.time()
            self._scroll_buffer_x = 0
            self._scroll_buffer_y = 0

        self._scroll_buffer_x += dx
        self._scroll_buffer_y += dy
        self._scroll_last_x = x
        self._scroll_last_y = y

        # Flush after 300ms of no scroll events
        self._scroll_timer = threading.Timer(0.3, self._flush_scroll_buffer)
        self._scroll_timer.daemon = True
        self._scroll_timer.start()

    def _flush_scroll_buffer(self) -> None:
        """Flush accumulated scroll events as a single action."""
        if self._scroll_start_time is None:
            return

        dx = self._scroll_buffer_x
        dy = self._scroll_buffer_y
        x = self._scroll_last_x or 0
        y = self._scroll_last_y or 0

        self._flush_text_buffer()

        action = RecordedAction(
            id=self._generate_action_id(),
            action_type=ActionType.MOUSE_SCROLL,
            timestamp=self._scroll_start_time,
            x=x,
            y=y,
            dx=dx,
            dy=dy,
            delay_before=self._get_delay(),
            description=f"Scroll ({dx}, {dy}) at ({x}, {y})"
        )
        self._record_action(action)

        self._scroll_start_time = None
        self._scroll_buffer_x = 0
        self._scroll_buffer_y = 0
        self._scroll_timer = None
    
    def _on_key_press(self, key) -> None:
        """Handle key press events."""
        try:
            # Get key character
            if hasattr(key, 'char') and key.char:
                char = key.char
                
                # Accumulate text
                if self._text_buffer_start_time is None:
                    self._text_buffer_start_time = time.time()
                self._text_buffer += char
                
            else:
                # Special key
                key_name = key.name if hasattr(key, 'name') else str(key)
                
                # Handle special keys that should flush text buffer
                if key_name in ['enter', 'return', 'tab', 'escape']:
                    self._flush_text_buffer()
                    
                    action = RecordedAction(
                        id=self._generate_action_id(),
                        action_type=ActionType.KEY_PRESS,
                        timestamp=time.time(),
                        key=key_name,
                        delay_before=self._get_delay(),
                        description=f"Press {key_name}"
                    )
                    self._record_action(action)
                    
                elif key_name == 'backspace':
                    # Handle backspace in text buffer
                    if self._text_buffer:
                        self._text_buffer = self._text_buffer[:-1]
                    else:
                        action = RecordedAction(
                            id=self._generate_action_id(),
                            action_type=ActionType.KEY_PRESS,
                            timestamp=time.time(),
                            key=key_name,
                            delay_before=self._get_delay(),
                            description=f"Press {key_name}"
                        )
                        self._record_action(action)
                        
                elif key_name == 'space':
                    if self._text_buffer_start_time is None:
                        self._text_buffer_start_time = time.time()
                    self._text_buffer += ' '
                    
        except Exception as e:
            logger.error(f"Error handling key press: {e}")
    
    def _on_key_release(self, key) -> None:
        """Handle key release events."""
        # We primarily track presses, not releases
        # But we can use this to detect held keys if needed
        pass
    
    # ===== Win32 Scroll Hook (reliable scroll capture on Windows) =====

    def _start_win32_scroll_hook(self) -> None:
        """Install a Win32 low-level mouse hook to capture scroll events reliably."""
        import platform
        if platform.system() != "Windows":
            return

        import ctypes
        import ctypes.wintypes

        WH_MOUSE_LL = 14
        WM_MOUSEWHEEL = 0x020A
        WM_MOUSEHWHEEL = 0x020E

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ('pt', ctypes.wintypes.POINT),
                ('mouseData', ctypes.wintypes.DWORD),
                ('flags', ctypes.wintypes.DWORD),
                ('time', ctypes.wintypes.DWORD),
                ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong)),
            ]

        LowLevelMouseProc = ctypes.WINFUNCTYPE(
            ctypes.c_long,
            ctypes.c_int,
            ctypes.wintypes.WPARAM,
            ctypes.wintypes.LPARAM,
        )

        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int, LowLevelMouseProc,
            ctypes.wintypes.HINSTANCE, ctypes.wintypes.DWORD,
        ]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.argtypes = [
            ctypes.c_void_p, ctypes.c_int,
            ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM,
        ]
        user32.CallNextHookEx.restype = ctypes.c_long
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(ctypes.wintypes.MSG),
            ctypes.wintypes.HWND, ctypes.c_uint, ctypes.c_uint,
        ]
        user32.PostThreadMessageW.argtypes = [
            ctypes.wintypes.DWORD, ctypes.c_uint,
            ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM,
        ]

        recorder_self = self  # prevent closure issues

        @LowLevelMouseProc
        def low_level_mouse_proc(nCode, wParam, lParam):
            if nCode >= 0 and wParam in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
                ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                delta = ctypes.c_short(ms.mouseData >> 16).value
                clicks = delta // 120 if delta != 0 else 0
                x, y = ms.pt.x, ms.pt.y
                if wParam == WM_MOUSEWHEEL:
                    recorder_self._on_mouse_scroll(x, y, 0, clicks)
                else:
                    recorder_self._on_mouse_scroll(x, y, clicks, 0)
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        self._scroll_hook_proc_ref = low_level_mouse_proc
        self._scroll_user32 = user32  # prevent GC

        def hook_thread():
            self._scroll_hook = user32.SetWindowsHookExW(
                WH_MOUSE_LL, self._scroll_hook_proc_ref, None, 0,
            )
            if not self._scroll_hook:
                logger.warning("Failed to install Win32 scroll hook")
                return

            logger.debug(f"Win32 scroll hook installed: {self._scroll_hook}")

            # Message loop required for the hook to receive events
            msg = ctypes.wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

        self._scroll_hook_thread = threading.Thread(target=hook_thread, daemon=True)
        self._scroll_hook_thread.start()

    def _stop_win32_scroll_hook(self) -> None:
        """Remove the Win32 scroll hook."""
        if self._scroll_hook and hasattr(self, '_scroll_user32'):
            user32 = self._scroll_user32
            user32.UnhookWindowsHookEx(self._scroll_hook)
            # Post WM_QUIT to break the message loop
            if self._scroll_hook_thread and self._scroll_hook_thread.is_alive():
                thread_id = self._scroll_hook_thread.ident
                if thread_id:
                    user32.PostThreadMessageW(int(thread_id), 0x0012, 0, 0)
                self._scroll_hook_thread.join(timeout=2)
            self._scroll_hook = None
            self._scroll_hook_thread = None
            self._scroll_hook_proc_ref = None
            self._scroll_user32 = None
            logger.debug("Win32 scroll hook removed")

    def get_actions(self) -> List[RecordedAction]:
        """Get current recorded actions."""
        return self._actions.copy()

    def close(self) -> None:
        """Clean up resources."""
        if self._recording:
            self.stop_recording()
        self._screen_capture.close()
