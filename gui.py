"""
Desktop Automation Recorder - Complete GUI Module
Production-grade PyQt6 desktop application.
"""
import sys
import os
import uuid
import logging
import subprocess
import time
import webbrowser
import winreg
from pathlib import Path
from datetime import datetime
from typing import Optional, List

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QTextEdit, QListWidget, QListWidgetItem,
    QTabWidget, QGroupBox, QFormLayout, QComboBox, QSpinBox, QDoubleSpinBox,
    QCheckBox, QMessageBox, QInputDialog, QDialog, QDialogButtonBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame, QSystemTrayIcon, QMenu,
    QFileDialog, QScrollArea
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QTimer, QRect, QPropertyAnimation, QPoint
from PyQt6.QtGui import QFont, QAction, QPixmap, QDesktopServices, QScreen, QCursor
from PyQt6.QtCore import QUrl

from recorder import ActionRecorder, RecordedAction, Recording, ActionType
from playback import PlaybackEngine, PlaybackReport, PlaybackStatus
from credentials import CredentialManager, Credential
from scheduler import AutomationScheduler, Schedule, ScheduleFrequency, EmailConfig
from database import Database, RecordingModel
from ai_engine import AIEngine, AuditContext, AuditReport, PerformanceMetrics, TimingData, StepAnalysis
from report_generator import ReportGenerator

logger = logging.getLogger(__name__)

# Application paths
APP_DIR = Path.home() / ".desktop-automation"
DATA_DIR = APP_DIR / "data"
RECORDINGS_DIR = DATA_DIR / "recordings"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
REPORTS_DIR = DATA_DIR / "reports"
DB_PATH = DATA_DIR / "automation.db"

for d in [APP_DIR, DATA_DIR, RECORDINGS_DIR, SCREENSHOTS_DIR, REPORTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)


def _find_chrome() -> Optional[str]:
    """Locate Chrome executable on Windows."""
    # Check common install paths
    common_paths = [
        Path(os.environ.get("PROGRAMFILES", "C:\\Program Files")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    for p in common_paths:
        if p.exists():
            return str(p)

    # Try Windows registry
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


class RecordingThread(QThread):
    """Background thread for recording user actions."""
    action_recorded = pyqtSignal(object)
    recording_stopped = pyqtSignal(list)
    
    def __init__(self, recorder: ActionRecorder):
        super().__init__()
        self.recorder = recorder
        self._running = False
    
    def run(self):
        self._running = True
        self.recorder.on_action = lambda a: self.action_recorded.emit(a)
        self.recorder.start_recording()
        while self._running:
            self.msleep(100)
    
    def stop(self):
        self._running = False
        actions = self.recorder.stop_recording()
        self.recording_stopped.emit(actions)


class PlaybackThread(QThread):
    """Background thread for playback."""
    playback_finished = pyqtSignal(object)
    
    def __init__(self, engine: PlaybackEngine, recording: Recording):
        super().__init__()
        self.engine = engine
        self.recording = recording
    
    def run(self):
        report = self.engine.execute(self.recording)
        self.playback_finished.emit(report)


class CredentialDialog(QDialog):
    """Dialog for managing credentials."""
    
    def __init__(self, parent=None, credential: Optional[Credential] = None):
        super().__init__(parent)
        self.credential = credential
        self.setWindowTitle("Credential" if not credential else "Edit Credential")
        self.setMinimumWidth(400)
        
        layout = QVBoxLayout(self)
        form = QFormLayout()
        
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g., Company Portal")
        form.addRow("Name:", self.name_input)
        
        self.username_input = QLineEdit()
        form.addRow("Username:", self.username_input)
        
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Password:", self.password_input)
        
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://... (optional)")
        form.addRow("URL:", self.url_input)
        
        layout.addLayout(form)
        
        if credential:
            self.name_input.setText(credential.name)
            self.name_input.setEnabled(False)
            self.username_input.setText(credential.username)
            self.url_input.setText(credential.url or "")
        
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
    
    def get_credential(self) -> Optional[Credential]:
        name = self.name_input.text().strip()
        username = self.username_input.text().strip()
        password = self.password_input.text()
        if not name or not username or not password:
            return None
        return Credential(
            name=name, username=username, _password=password,
            url=self.url_input.text().strip() or None
        )


class ScheduleDialog(QDialog):
    """Dialog for creating schedules."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create Schedule")
        self.setMinimumWidth(400)
        
        layout = QVBoxLayout(self)
        form = QFormLayout()
        
        self.name_input = QLineEdit()
        form.addRow("Name:", self.name_input)
        
        self.frequency_combo = QComboBox()
        self.frequency_combo.addItems(["Once", "Hourly", "Daily", "Weekly", "Monthly", "Custom (Cron)"])
        self.frequency_combo.currentTextChanged.connect(self._toggle_cron)
        form.addRow("Frequency:", self.frequency_combo)
        
        self.cron_input = QLineEdit()
        self.cron_input.setPlaceholderText("0 9 * * *")
        self.cron_input.setVisible(False)
        form.addRow("Cron:", self.cron_input)
        
        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("email@example.com")
        form.addRow("Email:", self.email_input)
        
        layout.addLayout(form)
        
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
    
    def _toggle_cron(self, text):
        self.cron_input.setVisible(text == "Custom (Cron)")
    
    def get_data(self) -> Optional[dict]:
        name = self.name_input.text().strip()
        emails = [e.strip() for e in self.email_input.text().split(",") if e.strip()]
        if not name or not emails:
            return None
        
        freq_map = {
            "Once": ScheduleFrequency.ONCE, "Hourly": ScheduleFrequency.HOURLY,
            "Daily": ScheduleFrequency.DAILY, "Weekly": ScheduleFrequency.WEEKLY,
            "Monthly": ScheduleFrequency.MONTHLY, "Custom (Cron)": ScheduleFrequency.CUSTOM
        }
        freq = freq_map[self.frequency_combo.currentText()]
        cron = self.cron_input.text().strip() if freq == ScheduleFrequency.CUSTOM else None
        
        return {"name": name, "frequency": freq, "cron_expression": cron, "email_recipients": emails}


class EmailConfigDialog(QDialog):
    """Email configuration dialog."""
    
    def __init__(self, parent=None, config: Optional[EmailConfig] = None):
        super().__init__(parent)
        self.setWindowTitle("Email Settings")
        self.setMinimumWidth(400)
        
        layout = QVBoxLayout(self)
        form = QFormLayout()
        
        self.host_input = QLineEdit("smtp.gmail.com")
        form.addRow("SMTP Host:", self.host_input)
        
        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(587)
        form.addRow("Port:", self.port_input)
        
        self.user_input = QLineEdit()
        form.addRow("Username:", self.user_input)
        
        self.pass_input = QLineEdit()
        self.pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Password:", self.pass_input)
        
        self.from_input = QLineEdit()
        form.addRow("From:", self.from_input)
        
        layout.addLayout(form)
        
        if config:
            self.host_input.setText(config.smtp_host)
            self.port_input.setValue(config.smtp_port)
            self.user_input.setText(config.username)
            self.from_input.setText(config.from_address)
        
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
    
    def get_config(self) -> Optional[EmailConfig]:
        if not all([self.host_input.text(), self.user_input.text(), 
                    self.pass_input.text(), self.from_input.text()]):
            return None
        return EmailConfig(
            smtp_host=self.host_input.text().strip(),
            smtp_port=self.port_input.value(),
            username=self.user_input.text().strip(),
            password=self.pass_input.text(),
            from_address=self.from_input.text().strip()
        )


class NewRecordingDialog(QDialog):
    """Dialog shown when starting a new recording — collects name, URL, and email."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Recording")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g., Login Flow Test")
        form.addRow("Recording Name:", self.name_input)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://... (optional)")
        form.addRow("Starting URL:", self.url_input)

        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("user@example.com (comma-separated for multiple)")
        form.addRow("Send report to:", self.email_input)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_name(self) -> str:
        return self.name_input.text().strip()

    def get_url(self) -> str:
        return self.url_input.text().strip()

    def get_emails(self) -> list:
        return [e.strip() for e in self.email_input.text().split(",") if e.strip()]


class AuditContextDialog(QDialog):
    """Dialog shown after recording stops to collect audit purpose and goal."""

    def __init__(self, parent=None, purpose: str = "", goal: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Audit Context")
        self.setMinimumWidth(450)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Provide context for AI audit analysis (optional):"))

        layout.addWidget(QLabel("What is the purpose of this recording?"))
        self.purpose_input = QTextEdit()
        self.purpose_input.setPlaceholderText("e.g., Verify login flow works correctly after deployment")
        self.purpose_input.setMaximumHeight(80)
        if purpose:
            self.purpose_input.setPlainText(purpose)
        layout.addWidget(self.purpose_input)

        layout.addWidget(QLabel("What are you testing/verifying?"))
        self.goal_input = QTextEdit()
        self.goal_input.setPlaceholderText("e.g., User can log in with valid credentials and see dashboard")
        self.goal_input.setMaximumHeight(80)
        if goal:
            self.goal_input.setPlainText(goal)
        layout.addWidget(self.goal_input)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_purpose(self) -> str:
        return self.purpose_input.toPlainText().strip()

    def get_goal(self) -> str:
        return self.goal_input.toPlainText().strip()


class AuditThread(QThread):
    """Background thread that runs AI audit analysis, generates HTML report, and emails it."""
    progress = pyqtSignal(int, int)  # done, total
    finished = pyqtSignal(str)       # report path
    error = pyqtSignal(str)

    def __init__(
        self,
        api_key: str,
        recording: Recording,
        report: PlaybackReport,
        playback_screenshots: dict,
        output_dir: str,
        email_config: Optional[EmailConfig] = None,
        email_recipients: Optional[list] = None,
    ):
        super().__init__()
        self._api_key = api_key
        self._recording = recording
        self._report = report
        self._playback_screenshots = playback_screenshots
        self._output_dir = output_dir
        self._email_config = email_config
        self._email_recipients = email_recipients or []

    def _send_report_email(self, report_html: str, report_path: str, recording_name: str):
        """Send the HTML audit report embedded in the email body with HTML file attached."""
        if not self._email_config or not self._email_recipients:
            return

        import smtplib
        import ssl
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.application import MIMEApplication

        try:
            cfg = self._email_config
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

            msg = MIMEMultipart("mixed")
            msg["Subject"] = f"AI Audit Report: {recording_name} [{timestamp}]"
            msg["From"] = cfg.from_address
            msg["To"] = ", ".join(self._email_recipients)

            # Embed full HTML report in email body
            msg.attach(MIMEText(report_html, "html", "utf-8"))

            # Also attach the HTML file for download
            report_data = Path(report_path).read_bytes()
            attachment = MIMEApplication(report_data, _subtype="html")
            attachment.add_header(
                "Content-Disposition", "attachment",
                filename=Path(report_path).name
            )
            msg.attach(attachment)

            context = ssl.create_default_context()
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port) as server:
                if cfg.use_tls:
                    server.starttls(context=context)
                server.login(cfg.username, cfg.password)
                server.send_message(msg)

            logger.info(f"Audit report emailed to {self._email_recipients}")

        except Exception as e:
            logger.error(f"Failed to email audit report: {e}")

    def run(self):
        try:
            engine = AIEngine(self._api_key)
            rec = self._recording
            report = self._report

            context = AuditContext(
                recording_name=rec.name,
                recording_id=rec.id,
                purpose=rec.audit_purpose or "",
                verification_goal=rec.audit_verification_goal or "",
                url=rec.url,
                total_steps=len(rec.actions),
            )

            # Build step analyses with timing (no per-step AI calls)
            step_analyses = []
            screenshot_steps = []  # only steps with playback screenshots
            for idx, action in enumerate(rec.actions):
                play_ss = self._playback_screenshots.get(action.id)
                sa = StepAnalysis(
                    step_index=idx,
                    action_id=action.id,
                    action_type=action.action_type.value,
                    action_description=action.description or "",
                    play_screenshot_path=play_ss,
                    has_screenshot=bool(play_ss),
                )
                if idx < len(report.results):
                    pr = report.results[idx]
                    sa.timing = TimingData(
                        recording_timestamp=action.timestamp,
                        playback_duration_ms=pr.duration_ms,
                        page_load_time_ms=pr.page_load_time_ms,
                    )
                step_analyses.append(sa)

                if play_ss and Path(play_ss).exists():
                    screenshot_steps.append({
                        "index": idx,
                        "action_type": action.action_type.value,
                        "description": action.description or "",
                        "play_screenshot": play_ss,
                    })

            self.progress.emit(0, 2)

            # Performance metrics
            perf = PerformanceMetrics()
            if rec.actions:
                perf.total_recording_time_s = rec.actions[-1].timestamp - rec.actions[0].timestamp
            perf.total_playback_time_s = report.duration_seconds
            durations = [r.duration_ms for r in report.results]
            if durations:
                perf.avg_step_duration_ms = sum(durations) / len(durations)
                perf.slowest_step_duration_ms = max(durations)
                perf.slowest_step_index = durations.index(perf.slowest_step_duration_ms)
            for idx, r in enumerate(report.results):
                if r.page_load_time_ms is not None:
                    perf.page_load_times.append({"step": idx + 1, "time_ms": r.page_load_time_ms})

            # API call 1: Analyze screenshots (single call with all images)
            screenshot_analysis = engine.analyze_screenshots(screenshot_steps, context, perf)
            self.progress.emit(1, 2)

            # API call 2: Executive summary
            executive_summary = engine.generate_executive_summary(
                context, perf, screenshot_analysis,
                total_actions=report.total_actions,
                completed_actions=report.completed_actions,
                failed_actions=report.failed_actions,
            )
            self.progress.emit(2, 2)

            # Assemble report
            from datetime import datetime as dt
            audit_report = AuditReport(
                context=context,
                step_analyses=step_analyses,
                findings=[],
                performance=perf,
                executive_summary=executive_summary,
                screenshot_analysis=screenshot_analysis,
                generated_at=dt.now().strftime("%Y-%m-%d %H:%M:%S"),
                ai_model_used=engine.ai_model_used,
                total_api_calls=engine.total_api_calls,
                estimated_cost_usd=engine.estimated_cost_usd,
            )

            # Generate HTML
            gen = ReportGenerator()
            output_path = str(
                Path(self._output_dir) / f"audit_{rec.id}_{dt.now().strftime('%Y%m%d_%H%M%S')}.html"
            )
            gen.generate(audit_report, output_path)

            # Email the full HTML report embedded in body + attached as file
            if self._email_recipients:
                report_html = gen.get_html(audit_report)
                self._send_report_email(report_html, output_path, rec.name)

            self.finished.emit(output_path)

        except Exception as e:
            logger.error(f"Audit failed: {e}", exc_info=True)
            self.error.emit(str(e))


class RecordingToolbar(QWidget):
    """Floating toolbar shown during recording."""
    screenshot_requested = pyqtSignal()
    password_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    moved = pyqtSignal()

    def __init__(self, recording_name: str, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setFixedSize(450, 44)
        self._drag_pos = None
        self._action_count = 0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(8)

        # Pulsing REC label
        self._rec_label = QLabel("\u25cf REC")
        self._rec_label.setStyleSheet("color: #ff3333; font-weight: bold; font-size: 14px;")
        layout.addWidget(self._rec_label)
        self._pulse_visible = True
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._pulse)
        self._pulse_timer.start(600)

        screenshot_btn = QPushButton("\U0001f4f8 Screenshot")
        screenshot_btn.clicked.connect(self.screenshot_requested.emit)
        layout.addWidget(screenshot_btn)

        password_btn = QPushButton("\U0001f511 Password")
        password_btn.clicked.connect(self.password_requested.emit)
        layout.addWidget(password_btn)

        stop_btn = QPushButton("\u23f9 Stop")
        stop_btn.clicked.connect(self.stop_requested.emit)
        layout.addWidget(stop_btn)

        self._action_label = QLabel("Actions: 0")
        self._action_label.setStyleSheet("color: white; font-size: 12px;")
        layout.addWidget(self._action_label)

        self.setStyleSheet("""
            RecordingToolbar {
                background: #1a1a2e;
                border: 1px solid #4f46e5;
                border-radius: 8px;
            }
            QPushButton {
                background: #2d2d44; color: white; border: none;
                padding: 6px 10px; border-radius: 4px; font-size: 12px;
            }
            QPushButton:hover { background: #3d3d5c; }
        """)

        # Position at top-center of screen
        screen = QApplication.primaryScreen()
        if screen:
            sg = screen.availableGeometry()
            self.move(sg.x() + (sg.width() - self.width()) // 2, sg.y() + 6)

    def update_action_count(self, count: int):
        self._action_count = count
        self._action_label.setText(f"Actions: {count}")

    def get_rect(self) -> QRect:
        return QRect(self.pos(), self.size())

    def _pulse(self):
        self._pulse_visible = not self._pulse_visible
        self._rec_label.setStyleSheet(
            f"color: {'#ff3333' if self._pulse_visible else '#661111'}; font-weight: bold; font-size: 14px;"
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        self.moved.emit()

    def closeEvent(self, event):
        self._pulse_timer.stop()
        super().closeEvent(event)


class MainWindow(QMainWindow):
    """Main application window."""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Desktop Automation Recorder")
        self.setMinimumSize(1100, 700)
        
        # Initialize components
        self.db = Database(DB_PATH)
        self.credential_manager = CredentialManager(DATA_DIR / "credentials")
        self.recorder = ActionRecorder(storage_dir=SCREENSHOTS_DIR)
        self.playback_engine = PlaybackEngine(
            credential_manager=self.credential_manager,
            storage_dir=SCREENSHOTS_DIR
        )
        
        email_data = self.db.get_setting("email_config")
        email_config = EmailConfig.from_dict(email_data) if email_data else None
        
        self.scheduler = AutomationScheduler(
            storage_dir=DATA_DIR / "scheduler",
            credential_manager=self.credential_manager,
            recordings_dir=RECORDINGS_DIR,
            email_config=email_config
        )
        
        self.current_recording = None
        self.recording_thread = None
        self.is_recording = False
        self.recording_toolbar = None
        
        self._setup_ui()
        self._load_data()
        self.scheduler.start()
    
    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # Sidebar
        sidebar = self._create_sidebar()
        main_layout.addWidget(sidebar)
        
        # Tabs
        self.tabs = QTabWidget()
        self.tabs.addTab(self._create_recordings_tab(), "📹 Recordings")
        self.tabs.addTab(self._create_screenshots_tab(), "📸 Screenshots")
        self.tabs.addTab(self._create_schedules_tab(), "📅 Schedules")
        self.tabs.addTab(self._create_credentials_tab(), "🔐 Credentials")
        self.tabs.addTab(self._create_history_tab(), "📊 History")
        self.tabs.addTab(self._create_settings_tab(), "⚙️ Settings")
        main_layout.addWidget(self.tabs, 1)
        
        self.statusBar().showMessage("Ready")
        self._apply_styles()
    
    def _create_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setFixedWidth(220)
        sidebar.setObjectName("sidebar")
        
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 16, 12, 16)
        
        title = QLabel("🤖 Automation")
        title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        layout.addWidget(title)
        
        layout.addSpacing(20)
        
        self.record_btn = QPushButton("⏺️  Start Recording")
        self.record_btn.setObjectName("primaryBtn")
        self.record_btn.clicked.connect(self._toggle_recording)
        layout.addWidget(self.record_btn)
        
        self.screenshot_btn = QPushButton("📸  Screenshot")
        self.screenshot_btn.clicked.connect(self._take_screenshot)
        self.screenshot_btn.setEnabled(False)
        layout.addWidget(self.screenshot_btn)
        
        self.cred_btn = QPushButton("🔑  Mark Password")
        self.cred_btn.clicked.connect(self._mark_password)
        self.cred_btn.setEnabled(False)
        layout.addWidget(self.cred_btn)
        
        layout.addSpacing(10)
        self.recording_label = QLabel("")
        self.recording_label.setWordWrap(True)
        layout.addWidget(self.recording_label)
        
        layout.addStretch()
        return sidebar
    
    def _create_recordings_tab(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        
        # List
        left = QVBoxLayout()
        left.addWidget(QLabel("Recordings"))
        self.recordings_list = QListWidget()
        self.recordings_list.currentItemChanged.connect(self._on_recording_selected)
        left.addWidget(self.recordings_list)
        layout.addLayout(left, 1)
        
        # Details
        right = QVBoxLayout()
        self.details_group = QGroupBox("Details")
        details_form = QFormLayout(self.details_group)
        self.detail_name = QLabel("-")
        details_form.addRow("Name:", self.detail_name)
        self.detail_url = QLabel("-")
        details_form.addRow("URL:", self.detail_url)
        self.detail_actions = QLabel("-")
        details_form.addRow("Actions:", self.detail_actions)
        right.addWidget(self.details_group)
        
        self.actions_table = QTableWidget()
        self.actions_table.setColumnCount(3)
        self.actions_table.setHorizontalHeaderLabels(["#", "Type", "Description"])
        self.actions_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        right.addWidget(self.actions_table)
        
        btn_row = QHBoxLayout()
        self.play_btn = QPushButton("▶️ Play")
        self.play_btn.clicked.connect(self._play_recording)
        self.play_btn.setEnabled(False)
        btn_row.addWidget(self.play_btn)
        
        self.schedule_btn = QPushButton("📅 Schedule")
        self.schedule_btn.clicked.connect(self._schedule_recording)
        self.schedule_btn.setEnabled(False)
        btn_row.addWidget(self.schedule_btn)
        
        self.delete_btn = QPushButton("🗑️ Delete")
        self.delete_btn.clicked.connect(self._delete_recording)
        self.delete_btn.setEnabled(False)
        btn_row.addWidget(self.delete_btn)

        self.edit_context_btn = QPushButton("📝 Edit AI Context")
        self.edit_context_btn.clicked.connect(self._edit_audit_context)
        self.edit_context_btn.setEnabled(False)
        btn_row.addWidget(self.edit_context_btn)
        right.addLayout(btn_row)
        
        layout.addLayout(right, 2)
        return widget
    
    def _create_screenshots_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Top bar
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Screenshots from playback runs"))
        top_row.addStretch()
        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self._load_screenshots)
        top_row.addWidget(refresh_btn)
        open_btn = QPushButton("📂 Open Folder")
        open_btn.clicked.connect(self._open_screenshot_folder)
        top_row.addWidget(open_btn)
        layout.addLayout(top_row)

        # Screenshot list
        self.screenshots_list = QListWidget()
        self.screenshots_list.currentItemChanged.connect(self._on_screenshot_selected)
        layout.addWidget(self.screenshots_list, 1)

        # Preview area
        self.screenshot_preview = QLabel("Select a screenshot to preview")
        self.screenshot_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.screenshot_preview.setMinimumHeight(300)
        self.screenshot_preview.setStyleSheet("background: #222; color: #aaa; border: 1px solid #444;")
        layout.addWidget(self.screenshot_preview, 2)

        # Bottom buttons
        btn_row = QHBoxLayout()
        self.open_ss_btn = QPushButton("🔍 Open Full Size")
        self.open_ss_btn.clicked.connect(self._open_selected_screenshot)
        self.open_ss_btn.setEnabled(False)
        btn_row.addWidget(self.open_ss_btn)
        self.delete_ss_btn = QPushButton("🗑️ Delete")
        self.delete_ss_btn.clicked.connect(self._delete_selected_screenshot)
        self.delete_ss_btn.setEnabled(False)
        btn_row.addWidget(self.delete_ss_btn)
        layout.addLayout(btn_row)

        return widget

    def _load_screenshots(self):
        self.screenshots_list.clear()
        folder = self._get_screenshot_folder()
        if not folder.exists():
            return
        pngs = sorted(folder.glob("screenshot_*.png"), reverse=True)
        for p in pngs:
            item = QListWidgetItem(p.name)
            item.setData(Qt.ItemDataRole.UserRole, str(p))
            self.screenshots_list.addItem(item)

    def _on_screenshot_selected(self, current, prev):
        enabled = current is not None
        self.open_ss_btn.setEnabled(enabled)
        self.delete_ss_btn.setEnabled(enabled)
        if not current:
            self.screenshot_preview.setPixmap(QPixmap())
            return
        path = current.data(Qt.ItemDataRole.UserRole)
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                self.screenshot_preview.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.screenshot_preview.setPixmap(scaled)

    def _open_selected_screenshot(self):
        item = self.screenshots_list.currentItem()
        if item:
            path = item.data(Qt.ItemDataRole.UserRole)
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _delete_selected_screenshot(self):
        item = self.screenshots_list.currentItem()
        if not item:
            return
        if QMessageBox.question(self, "Delete", "Delete this screenshot?") == QMessageBox.StandardButton.Yes:
            path = item.data(Qt.ItemDataRole.UserRole)
            Path(path).unlink(missing_ok=True)
            self._load_screenshots()

    def _create_schedules_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        self.schedules_table = QTableWidget()
        self.schedules_table.setColumnCount(6)
        self.schedules_table.setHorizontalHeaderLabels([
            "Name", "Recording", "Frequency", "Next Run", "Success", "Active"
        ])
        self.schedules_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.schedules_table)
        
        btn_row = QHBoxLayout()
        btn_row.addWidget(QPushButton("🔄 Refresh", clicked=self._load_schedules))
        btn_row.addWidget(QPushButton("▶️ Run Now", clicked=self._run_schedule_now))
        btn_row.addWidget(QPushButton("⏸️ Toggle", clicked=self._toggle_schedule))
        btn_row.addWidget(QPushButton("🗑️ Delete", clicked=self._delete_schedule))
        layout.addLayout(btn_row)
        
        return widget
    
    def _create_credentials_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        layout.addWidget(QLabel("🔒 Credentials stored securely in system keychain"))
        
        self.creds_list = QListWidget()
        layout.addWidget(self.creds_list)
        
        btn_row = QHBoxLayout()
        btn_row.addWidget(QPushButton("➕ Add", clicked=self._add_credential))
        btn_row.addWidget(QPushButton("✏️ Edit", clicked=self._edit_credential))
        btn_row.addWidget(QPushButton("🗑️ Delete", clicked=self._delete_credential))
        layout.addLayout(btn_row)
        
        return widget
    
    def _create_history_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        self.history_table = QTableWidget()
        self.history_table.setColumnCount(5)
        self.history_table.setHorizontalHeaderLabels([
            "Recording", "Status", "Actions", "Duration", "Time"
        ])
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.history_table)
        
        layout.addWidget(QPushButton("🔄 Refresh", clicked=self._load_history))
        return widget
    
    def _create_settings_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Screenshot folder setting
        ss_group = QGroupBox("Screenshots")
        ss_layout = QVBoxLayout(ss_group)
        folder_row = QHBoxLayout()
        self.screenshot_folder_label = QLabel("Not configured (using default)")
        folder_row.addWidget(self.screenshot_folder_label, 1)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_screenshot_folder)
        folder_row.addWidget(browse_btn)
        ss_layout.addLayout(folder_row)
        open_folder_btn = QPushButton("Open Screenshot Folder")
        open_folder_btn.clicked.connect(self._open_screenshot_folder)
        ss_layout.addWidget(open_folder_btn)
        layout.addWidget(ss_group)

        # Load saved screenshot folder
        saved_folder = self.db.get_setting("screenshot_folder")
        if saved_folder:
            self.screenshot_folder_label.setText(saved_folder)

        # AI Audit settings
        ai_group = QGroupBox("AI Audit (Anthropic Claude)")
        ai_layout = QVBoxLayout(ai_group)

        self.ai_enabled_cb = QCheckBox("Enable AI audit after playback")
        self.ai_enabled_cb.setChecked(bool(self.db.get_setting("ai_audit_enabled", False)))
        self.ai_enabled_cb.stateChanged.connect(
            lambda state: self.db.set_setting("ai_audit_enabled", state == Qt.CheckState.Checked.value)
        )
        ai_layout.addWidget(self.ai_enabled_cb)

        api_row = QHBoxLayout()
        api_row.addWidget(QLabel("API Key:"))
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText("sk-ant-...")
        saved_key = self.db.get_setting("anthropic_api_key", "")
        if saved_key:
            self.api_key_input.setText(saved_key)
        api_row.addWidget(self.api_key_input, 1)
        save_key_btn = QPushButton("Save")
        save_key_btn.clicked.connect(self._save_api_key)
        api_row.addWidget(save_key_btn)
        ai_layout.addLayout(api_row)

        report_row = QHBoxLayout()
        report_row.addWidget(QLabel("Report folder:"))
        self.report_folder_label = QLabel(self.db.get_setting("report_output_dir") or "Default")
        report_row.addWidget(self.report_folder_label, 1)
        report_browse_btn = QPushButton("Browse...")
        report_browse_btn.clicked.connect(self._browse_report_folder)
        report_row.addWidget(report_browse_btn)
        ai_layout.addLayout(report_row)

        layout.addWidget(ai_group)

        email_group = QGroupBox("Email")
        email_layout = QVBoxLayout(email_group)
        self.email_status = QLabel("Not configured")
        email_layout.addWidget(self.email_status)
        email_layout.addWidget(QPushButton("Configure", clicked=self._configure_email))
        layout.addWidget(email_group)

        security_group = QGroupBox("Security")
        sec_layout = QVBoxLayout(security_group)
        sec_layout.addWidget(QPushButton("Set Master Password", clicked=self._set_master_password))
        layout.addWidget(security_group)

        layout.addStretch()
        return widget
    
    def _apply_styles(self):
        self.setStyleSheet("""
            #sidebar { background: #1a1a2e; }
            #sidebar QLabel { color: white; }
            #sidebar QPushButton { background: #2d2d44; color: white; border: none; padding: 10px; border-radius: 6px; }
            #sidebar QPushButton:hover { background: #3d3d5c; }
            #sidebar #primaryBtn { background: #4f46e5; }
            #sidebar #primaryBtn:hover { background: #4338ca; }
            QTabWidget::pane { background: white; border: none; }
            QGroupBox { font-weight: bold; border: 1px solid #ddd; border-radius: 6px; margin-top: 12px; padding-top: 12px; }
        """)
    
    def _load_data(self):
        self._load_recordings()
        self._load_screenshots()
        self._load_schedules()
        self._load_credentials()
        self._load_history()
        self._update_email_status()
    
    def _load_recordings(self):
        self.recordings_list.clear()
        for f in RECORDINGS_DIR.glob("*.json"):
            try:
                rec = Recording.load(f)
                item = QListWidgetItem(f"📹 {rec.name}")
                item.setData(Qt.ItemDataRole.UserRole, rec)
                self.recordings_list.addItem(item)
            except Exception as e:
                logger.error(f"Failed to load {f}: {e}")
    
    def _load_schedules(self):
        schedules = self.scheduler.list_schedules()
        self.schedules_table.setRowCount(len(schedules))
        for i, s in enumerate(schedules):
            self.schedules_table.setItem(i, 0, QTableWidgetItem(s.name))
            self.schedules_table.setItem(i, 1, QTableWidgetItem(s.recording_name))
            self.schedules_table.setItem(i, 2, QTableWidgetItem(s.frequency.value))
            self.schedules_table.setItem(i, 3, QTableWidgetItem(
                s.next_run.strftime("%m/%d %H:%M") if s.next_run else "-"))
            self.schedules_table.setItem(i, 4, QTableWidgetItem(f"{s.success_count}/{s.run_count}"))
            self.schedules_table.setItem(i, 5, QTableWidgetItem("✅" if s.is_active else "❌"))
            self.schedules_table.item(i, 0).setData(Qt.ItemDataRole.UserRole, s.id)
    
    def _load_credentials(self):
        self.creds_list.clear()
        for name in self.credential_manager.list_credentials():
            item = QListWidgetItem(f"🔑 {name}")
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.creds_list.addItem(item)
    
    def _load_history(self):
        runs = self.db.list_runs()
        self.history_table.setRowCount(len(runs))
        for i, r in enumerate(runs):
            rec = self.db.get_recording(r.recording_id)
            self.history_table.setItem(i, 0, QTableWidgetItem(rec.name if rec else "-"))
            status_icon = {"completed": "✅", "failed": "❌", "running": "🔄"}.get(r.status, "?")
            self.history_table.setItem(i, 1, QTableWidgetItem(f"{status_icon} {r.status}"))
            self.history_table.setItem(i, 2, QTableWidgetItem(f"{r.completed_actions}/{r.total_actions}"))
            self.history_table.setItem(i, 3, QTableWidgetItem(f"{r.duration_seconds:.1f}s" if r.duration_seconds else "-"))
            self.history_table.setItem(i, 4, QTableWidgetItem(r.started_at.strftime("%m/%d %H:%M") if r.started_at else "-"))
    
    def _update_email_status(self):
        cfg = self.db.get_setting("email_config")
        if isinstance(cfg, dict) and cfg.get("from_address"):
            self.email_status.setText(f"Configured: {cfg['from_address']}")
        else:
            self.email_status.setText("Not configured")
    
    # Recording
    def _toggle_recording(self):
        if self.is_recording:
            self._stop_recording()
        else:
            self._start_recording()
    
    def _start_recording(self):
        dlg = NewRecordingDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name = dlg.get_name()
        if not name:
            return
        url = dlg.get_url()
        emails = dlg.get_emails()

        self.current_recording = Recording(
            id=str(uuid.uuid4()), name=name, description="",
            created_at=datetime.now(), updated_at=datetime.now(),
            url=url or None, actions=[], email_recipients=emails
        )

        # Open Chrome to the URL before recording starts
        if url:
            chrome_path = _find_chrome()
            if chrome_path:
                subprocess.Popen([chrome_path, url])
            else:
                webbrowser.open(url)
            time.sleep(2)

        self.is_recording = True
        self.record_btn.setText("⏹️  Stop Recording")
        self.screenshot_btn.setEnabled(True)
        self.cred_btn.setEnabled(True)
        self.recording_label.setText(f"Recording: {name}\nActions: 0")

        self.recording_thread = RecordingThread(self.recorder)
        self.recording_thread.action_recorded.connect(self._on_action)
        self.recording_thread.recording_stopped.connect(self._on_stopped)
        self.recording_thread.start()

        # Show floating toolbar and hide main window
        self.recording_toolbar = RecordingToolbar(name)
        self.recording_toolbar.screenshot_requested.connect(self._take_screenshot)
        self.recording_toolbar.password_requested.connect(self._mark_password)
        self.recording_toolbar.stop_requested.connect(self._stop_recording)
        self.recording_toolbar.moved.connect(self._update_recorder_exclude_rect)
        self.recording_toolbar.show()
        self._update_recorder_exclude_rect()
        self.showMinimized()
        self.hide()

        self.statusBar().showMessage("Recording - perform your actions")
    
    def _update_recorder_exclude_rect(self):
        if self.recording_toolbar:
            r = self.recording_toolbar.get_rect()
            self.recorder._exclude_rect = (r.x(), r.y(), r.width(), r.height())
        else:
            self.recorder._exclude_rect = None

    def _stop_recording(self):
        if self.recording_thread:
            self.recording_thread.stop()
            self.recording_thread.wait()
    
    def _on_action(self, action):
        if self.current_recording:
            self.current_recording.actions.append(action)
            count = len(self.current_recording.actions)
            self.recording_label.setText(
                f"Recording: {self.current_recording.name}\n"
                f"Actions: {count}"
            )
            if self.recording_toolbar:
                self.recording_toolbar.update_action_count(count)
    
    def _on_stopped(self, actions):
        self.is_recording = False
        self.record_btn.setText("⏺️  Start Recording")
        self.screenshot_btn.setEnabled(False)
        self.cred_btn.setEnabled(False)

        # Close toolbar and restore main window
        if self.recording_toolbar:
            self.recording_toolbar.close()
            self.recording_toolbar = None
        self.recorder._exclude_rect = None
        self.show()
        self.raise_()
        self.activateWindow()
        
        if self.current_recording and actions:
            self.current_recording.actions = actions
            # Store step screenshots from recorder
            self.current_recording.step_screenshot_paths = self.recorder.step_screenshots

            # Show audit context dialog
            dlg = AuditContextDialog(self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self.current_recording.audit_purpose = dlg.get_purpose()
                self.current_recording.audit_verification_goal = dlg.get_goal()

            path = RECORDINGS_DIR / f"{self.current_recording.id}.json"
            self.current_recording.save(path)

            self.db.save_recording(
                id=self.current_recording.id, name=self.current_recording.name,
                description="", url=self.current_recording.url or "",
                action_count=len(actions), file_path=str(path),
                email_recipients=getattr(self.current_recording, 'email_recipients', None) or []
            )
            self.statusBar().showMessage(f"Saved {len(actions)} actions")
            self._load_recordings()

        self.current_recording = None
        self.recording_label.setText("")
    
    def _take_screenshot(self):
        if not self.is_recording:
            return

        # Hide toolbar before capture so it doesn't appear in screenshot
        if self.recording_toolbar:
            self.recording_toolbar.hide()
            QTimer.singleShot(200, self._do_screenshot_capture)
        else:
            self._do_screenshot_capture()

    def _do_screenshot_capture(self):
        import shutil
        filepath = self.recorder.add_screenshot_action()
        out_dir = self._get_screenshot_folder()
        out_dir.mkdir(parents=True, exist_ok=True)
        if filepath and Path(filepath).exists():
            dest = out_dir / Path(filepath).name
            if str(dest) != filepath:
                shutil.copy2(filepath, dest)
        # Re-show toolbar
        if self.recording_toolbar:
            self.recording_toolbar.show()
    
    def _mark_password(self):
        if not self.is_recording:
            return
        creds = self.credential_manager.list_credentials()
        if not creds:
            QMessageBox.warning(self, "No Credentials", "Add credentials first")
            return
        name, ok = QInputDialog.getItem(self, "Credential", "Select:", creds, 0, False)
        if not ok:
            return
        field, ok = QInputDialog.getItem(self, "Field", "Type:", ["password", "username"], 0, False)
        if ok:
            self.recorder.mark_sensitive_input(name, field)
            self.statusBar().showMessage(f"Next input: {field} from '{name}'")
    
    # Recording management
    def _on_recording_selected(self, current, prev):
        enabled = current is not None
        self.play_btn.setEnabled(enabled)
        self.schedule_btn.setEnabled(enabled)
        self.delete_btn.setEnabled(enabled)
        self.edit_context_btn.setEnabled(enabled)
        
        if not current:
            return
        
        rec = current.data(Qt.ItemDataRole.UserRole)
        self.detail_name.setText(rec.name)
        self.detail_url.setText(rec.url or "-")
        self.detail_actions.setText(str(len(rec.actions)))
        
        self.actions_table.setRowCount(len(rec.actions))
        for i, a in enumerate(rec.actions):
            self.actions_table.setItem(i, 0, QTableWidgetItem(str(i+1)))
            self.actions_table.setItem(i, 1, QTableWidgetItem(a.action_type.value))
            self.actions_table.setItem(i, 2, QTableWidgetItem(a.description or "-"))
    
    def _play_recording(self):
        item = self.recordings_list.currentItem()
        if not item:
            return
        rec = item.data(Qt.ItemDataRole.UserRole)
        
        if QMessageBox.question(self, "Play", f"Play '{rec.name}'?") != QMessageBox.StandardButton.Yes:
            return
        
        # Set screenshot output folder on engine before playback
        self.playback_engine.screenshot_output_dir = self._get_screenshot_folder()
        self.playback_thread = PlaybackThread(self.playback_engine, rec)
        self.playback_thread.playback_finished.connect(self._on_playback_done)
        self.playback_thread.start()
        self.statusBar().showMessage("Playing...")
    
    def _on_playback_done(self, report):
        self.db.save_run(
            recording_id=report.recording_id, schedule_id=None,
            status=report.status.value, total_actions=report.total_actions,
            completed_actions=report.completed_actions, failed_actions=report.failed_actions,
            duration_seconds=report.duration_seconds, error_message=report.error_message,
            screenshots=report.screenshots
        )
        self._load_history()
        self._load_screenshots()

        # Check if AI audit is enabled
        ai_enabled = self.db.get_setting("ai_audit_enabled", False)
        api_key = self.db.get_setting("anthropic_api_key", "")
        if ai_enabled and api_key:
            # Get the recording object
            item = self.recordings_list.currentItem()
            rec = item.data(Qt.ItemDataRole.UserRole) if item else None
            if rec:
                playback_ss = self.playback_engine.step_screenshots
                report_dir = self.db.get_setting("report_output_dir") or str(REPORTS_DIR)
                Path(report_dir).mkdir(parents=True, exist_ok=True)

                self._audit_progress_label = QLabel("AI Audit: 0/0 steps...")
                self.statusBar().addWidget(self._audit_progress_label)

                # Load email config for sending report
                email_cfg_data = self.db.get_setting("email_config")
                email_cfg = EmailConfig.from_dict(email_cfg_data) if email_cfg_data else None
                email_recipients = getattr(rec, 'email_recipients', None) or []

                self._audit_thread = AuditThread(
                    api_key=api_key,
                    recording=rec,
                    report=report,
                    playback_screenshots=playback_ss,
                    output_dir=report_dir,
                    email_config=email_cfg,
                    email_recipients=email_recipients,
                )
                self._audit_thread.progress.connect(self._on_audit_progress)
                self._audit_thread.finished.connect(self._on_audit_finished)
                self._audit_thread.error.connect(self._on_audit_error)
                self._audit_thread.start()
                self.statusBar().showMessage("Running AI audit analysis...")
                return

        if report.status == PlaybackStatus.COMPLETED:
            ss_count = len(report.screenshots)
            msg = f"Completed {report.completed_actions} actions"
            if ss_count:
                msg += f"\n{ss_count} screenshot(s) saved"
            QMessageBox.information(self, "Done", msg)
            if ss_count:
                self.tabs.setCurrentIndex(1)  # Switch to Screenshots tab
        else:
            QMessageBox.warning(self, "Failed", report.error_message or "Playback failed")

    def _on_audit_progress(self, done, total):
        if hasattr(self, '_audit_progress_label'):
            self._audit_progress_label.setText(f"AI Audit: {done}/{total} steps...")

    def _on_audit_finished(self, report_path):
        if hasattr(self, '_audit_progress_label'):
            self.statusBar().removeWidget(self._audit_progress_label)
        self.statusBar().showMessage(f"Audit report saved: {report_path}")

        # Open report in browser
        import webbrowser
        webbrowser.open(f"file:///{report_path}")

    def _on_audit_error(self, error_msg):
        if hasattr(self, '_audit_progress_label'):
            self.statusBar().removeWidget(self._audit_progress_label)
        self.statusBar().showMessage("AI audit failed")
        QMessageBox.warning(self, "Audit Error", f"AI audit failed:\n{error_msg}")
    
    def _schedule_recording(self):
        item = self.recordings_list.currentItem()
        if not item:
            return
        rec = item.data(Qt.ItemDataRole.UserRole)
        
        dlg = ScheduleDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        
        data = dlg.get_data()
        if not data:
            QMessageBox.warning(self, "Invalid", "Fill all fields")
            return
        
        self.scheduler.create_schedule(
            name=data["name"], recording_id=rec.id, recording_name=rec.name,
            frequency=data["frequency"], email_recipients=data["email_recipients"],
            cron_expression=data.get("cron_expression")
        )
        self._load_schedules()
        self.statusBar().showMessage(f"Schedule created: {data['name']}")
    
    def _delete_recording(self):
        item = self.recordings_list.currentItem()
        if not item:
            return
        rec = item.data(Qt.ItemDataRole.UserRole)
        
        if QMessageBox.question(self, "Delete", f"Delete '{rec.name}'?") != QMessageBox.StandardButton.Yes:
            return
        
        (RECORDINGS_DIR / f"{rec.id}.json").unlink(missing_ok=True)
        self.db.delete_recording(rec.id)
        self._load_recordings()
    
    def _edit_audit_context(self):
        item = self.recordings_list.currentItem()
        if not item:
            return
        rec = item.data(Qt.ItemDataRole.UserRole)

        dlg = AuditContextDialog(
            self,
            purpose=rec.audit_purpose or "",
            goal=rec.audit_verification_goal or "",
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        rec.audit_purpose = dlg.get_purpose()
        rec.audit_verification_goal = dlg.get_goal()

        # Save to JSON file
        path = RECORDINGS_DIR / f"{rec.id}.json"
        if path.exists():
            rec.save(path)

        # Update DB
        with self.db.get_session() as session:
            db_rec = session.query(RecordingModel).filter_by(id=rec.id).first()
            if db_rec:
                db_rec.audit_purpose = rec.audit_purpose
                db_rec.audit_verification_goal = rec.audit_verification_goal
                session.commit()

        # Update the item data
        item.setData(Qt.ItemDataRole.UserRole, rec)
        self.statusBar().showMessage("Audit context updated")

    # Schedules
    def _run_schedule_now(self):
        row = self.schedules_table.currentRow()
        if row >= 0:
            sid = self.schedules_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            self.scheduler.run_now(sid)
            self.statusBar().showMessage("Running...")
    
    def _toggle_schedule(self):
        row = self.schedules_table.currentRow()
        if row >= 0:
            sid = self.schedules_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            s = self.scheduler.get_schedule(sid)
            if s:
                self.scheduler.set_active(sid, not s.is_active)
                self._load_schedules()
    
    def _delete_schedule(self):
        row = self.schedules_table.currentRow()
        if row >= 0 and QMessageBox.question(self, "Delete", "Delete schedule?") == QMessageBox.StandardButton.Yes:
            sid = self.schedules_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            self.scheduler.delete_schedule(sid)
            self._load_schedules()
    
    # Credentials
    def _add_credential(self):
        dlg = CredentialDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            cred = dlg.get_credential()
            if cred:
                self.credential_manager.store_credential(cred)
                self._load_credentials()
    
    def _edit_credential(self):
        item = self.creds_list.currentItem()
        if not item:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        cred = self.credential_manager.get_credential(name)
        if cred:
            dlg = CredentialDialog(self, cred)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                new = dlg.get_credential()
                if new:
                    self.credential_manager.store_credential(new)
                    self._load_credentials()
    
    def _delete_credential(self):
        item = self.creds_list.currentItem()
        if item and QMessageBox.question(self, "Delete", "Delete?") == QMessageBox.StandardButton.Yes:
            self.credential_manager.delete_credential(item.data(Qt.ItemDataRole.UserRole))
            self._load_credentials()
    
    # Settings
    def _configure_email(self):
        cfg_data = self.db.get_setting("email_config")
        cfg = EmailConfig.from_dict(cfg_data) if cfg_data else None
        
        dlg = EmailConfigDialog(self, cfg)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_cfg = dlg.get_config()
            if new_cfg:
                self.db.set_setting("email_config", new_cfg.to_dict())
                self.scheduler.set_email_config(new_cfg)
                self._update_email_status()
    
    def _get_screenshot_folder(self) -> Path:
        """Get the configured screenshot folder, or the default."""
        saved = self.db.get_setting("screenshot_folder")
        if saved and Path(saved).is_dir():
            return Path(saved)
        return SCREENSHOTS_DIR

    def _browse_screenshot_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Screenshot Folder")
        if folder:
            self.db.set_setting("screenshot_folder", folder)
            self.screenshot_folder_label.setText(folder)
            self.statusBar().showMessage(f"Screenshot folder: {folder}")

    def _open_screenshot_folder(self):
        folder = self._get_screenshot_folder()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _save_api_key(self):
        key = self.api_key_input.text().strip()
        if key:
            self.db.set_setting("anthropic_api_key", key)
            self.statusBar().showMessage("API key saved")
        else:
            QMessageBox.warning(self, "Invalid", "Enter a valid API key")

    def _browse_report_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Report Output Folder")
        if folder:
            self.db.set_setting("report_output_dir", folder)
            self.report_folder_label.setText(folder)
            self.statusBar().showMessage(f"Report folder: {folder}")

    def _set_master_password(self):
        pwd, ok = QInputDialog.getText(self, "Password", "Master password:", QLineEdit.EchoMode.Password)
        if ok and pwd:
            self.credential_manager.set_master_password(pwd)
            self.statusBar().showMessage("Password set")
    
    def closeEvent(self, event):
        if self.is_recording:
            if QMessageBox.question(self, "Recording", "Stop recording?") != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._stop_recording()
        
        self.scheduler.stop()
        self.recorder.close()
        self.playback_engine.close()
        event.accept()
