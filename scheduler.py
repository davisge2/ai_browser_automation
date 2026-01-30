"""
Automation Scheduler
Manages scheduled execution of recordings with email notifications.

Features:
- Cron-style scheduling
- Persistent schedule storage
- Email notifications with screenshots
- Retry on failure
- System tray integration ready
"""
import json
import logging
import smtplib
import ssl
import time
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor

from recorder import Recording
from playback import PlaybackEngine, PlaybackReport, PlaybackStatus
from credentials import CredentialManager

logger = logging.getLogger(__name__)


class ScheduleFrequency(str, Enum):
    """Schedule frequency options."""
    ONCE = "once"
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    CUSTOM = "custom"  # Cron expression


@dataclass
class EmailConfig:
    """Email configuration for notifications."""
    smtp_host: str
    smtp_port: int
    username: str
    password: str
    from_address: str
    use_tls: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EmailConfig":
        return cls(**data)


@dataclass
class Schedule:
    """Scheduled automation configuration."""
    id: str
    name: str
    recording_id: str
    recording_name: str
    frequency: ScheduleFrequency
    cron_expression: Optional[str]  # For custom frequency
    email_recipients: List[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None
    run_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    
    # Execution options
    speed_multiplier: float = 1.0
    verify_visuals: bool = True
    retry_on_failure: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "recording_id": self.recording_id,
            "recording_name": self.recording_name,
            "frequency": self.frequency.value,
            "cron_expression": self.cron_expression,
            "email_recipients": self.email_recipients,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "next_run": self.next_run.isoformat() if self.next_run else None,
            "run_count": self.run_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "speed_multiplier": self.speed_multiplier,
            "verify_visuals": self.verify_visuals,
            "retry_on_failure": self.retry_on_failure,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Schedule":
        data["frequency"] = ScheduleFrequency(data["frequency"])
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        if data.get("last_run"):
            data["last_run"] = datetime.fromisoformat(data["last_run"])
        if data.get("next_run"):
            data["next_run"] = datetime.fromisoformat(data["next_run"])
        return cls(**data)


class EmailNotifier:
    """Sends email notifications with playback results."""
    
    def __init__(self, config: EmailConfig):
        self.config = config
    
    def send_report(
        self,
        recipients: List[str],
        schedule_name: str,
        report: PlaybackReport
    ) -> bool:
        """Send playback report via email."""
        try:
            msg = MIMEMultipart("related")
            msg["Subject"] = self._build_subject(schedule_name, report)
            msg["From"] = self.config.from_address
            msg["To"] = ", ".join(recipients)
            
            # HTML body
            html_body = self._build_html_body(schedule_name, report)
            
            msg_alt = MIMEMultipart("alternative")
            msg.attach(msg_alt)
            
            # Plain text fallback
            plain_text = self._build_plain_text(schedule_name, report)
            msg_alt.attach(MIMEText(plain_text, "plain"))
            msg_alt.attach(MIMEText(html_body, "html"))
            
            # Attach screenshots (limit to 5)
            for idx, screenshot_path in enumerate(report.screenshots[:5]):
                path = Path(screenshot_path)
                if path.exists():
                    with open(path, "rb") as f:
                        img = MIMEImage(f.read())
                        img.add_header("Content-ID", f"<screenshot{idx}>")
                        img.add_header(
                            "Content-Disposition",
                            "attachment",
                            filename=path.name
                        )
                        msg.attach(img)
            
            # Send
            context = ssl.create_default_context()
            
            with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port) as server:
                if self.config.use_tls:
                    server.starttls(context=context)
                server.login(self.config.username, self.config.password)
                server.send_message(msg)
            
            logger.info(f"Email sent to {recipients}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            return False
    
    def _build_subject(self, schedule_name: str, report: PlaybackReport) -> str:
        status_emoji = "✅" if report.status == PlaybackStatus.COMPLETED else "❌"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        return f"{status_emoji} [{schedule_name}] Automation Report - {timestamp}"
    
    def _build_html_body(self, schedule_name: str, report: PlaybackReport) -> str:
        status_color = "#10B981" if report.status == PlaybackStatus.COMPLETED else "#EF4444"
        status_text = "Completed Successfully" if report.status == PlaybackStatus.COMPLETED else "Failed"
        
        error_section = ""
        if report.error_message:
            error_section = f'''
            <div style="background: #FEE2E2; border-left: 4px solid #EF4444; padding: 16px; margin: 16px 0; border-radius: 4px;">
                <strong style="color: #991B1B;">Error:</strong>
                <pre style="margin: 8px 0 0 0; white-space: pre-wrap; color: #7F1D1D;">{report.error_message}</pre>
            </div>
            '''
        
        screenshots_section = ""
        if report.screenshots:
            screenshots_section = f'''
            <div style="margin-top: 24px;">
                <h3 style="color: #374151;">📸 Screenshots ({len(report.screenshots)})</h3>
                <p style="color: #6B7280; font-size: 14px;">Screenshots are attached to this email.</p>
            </div>
            '''
        
        return f'''
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"></head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #F3F4F6; margin: 0; padding: 24px;">
            <div style="max-width: 600px; margin: 0 auto; background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
                <div style="background: linear-gradient(135deg, #4F46E5 0%, #7C3AED 100%); padding: 32px; text-align: center;">
                    <h1 style="color: white; margin: 0;">🤖 Automation Report</h1>
                </div>
                <div style="padding: 32px;">
                    <div style="text-align: center; margin-bottom: 24px;">
                        <span style="background: {status_color}; color: white; padding: 8px 24px; border-radius: 24px; font-weight: 600;">
                            {status_text}
                        </span>
                    </div>
                    <div style="background: #F9FAFB; border-radius: 8px; padding: 20px;">
                        <table style="width: 100%;">
                            <tr><td style="padding: 8px 0; color: #6B7280;">Schedule:</td><td style="color: #111827; font-weight: 600;">{schedule_name}</td></tr>
                            <tr><td style="padding: 8px 0; color: #6B7280;">Recording:</td><td style="color: #111827;">{report.recording_name}</td></tr>
                            <tr><td style="padding: 8px 0; color: #6B7280;">Actions:</td><td style="color: #111827;">{report.completed_actions}/{report.total_actions}</td></tr>
                            <tr><td style="padding: 8px 0; color: #6B7280;">Success Rate:</td><td style="color: #111827;">{report.success_rate:.1f}%</td></tr>
                            <tr><td style="padding: 8px 0; color: #6B7280;">Duration:</td><td style="color: #111827;">{report.duration_seconds:.1f} seconds</td></tr>
                            <tr><td style="padding: 8px 0; color: #6B7280;">Executed:</td><td style="color: #111827;">{datetime.now().strftime("%B %d, %Y at %H:%M")}</td></tr>
                        </table>
                    </div>
                    {error_section}
                    {screenshots_section}
                </div>
                <div style="background: #F9FAFB; padding: 20px; text-align: center; border-top: 1px solid #E5E7EB;">
                    <p style="color: #9CA3AF; font-size: 12px; margin: 0;">Desktop Automation Recorder</p>
                </div>
            </div>
        </body>
        </html>
        '''
    
    def _build_plain_text(self, schedule_name: str, report: PlaybackReport) -> str:
        status = "COMPLETED" if report.status == PlaybackStatus.COMPLETED else "FAILED"
        
        text = f'''
Desktop Automation Report
=========================

Schedule: {schedule_name}
Recording: {report.recording_name}
Status: {status}
Actions: {report.completed_actions}/{report.total_actions}
Success Rate: {report.success_rate:.1f}%
Duration: {report.duration_seconds:.1f} seconds
Executed: {datetime.now().strftime("%Y-%m-%d %H:%M")}
'''
        
        if report.error_message:
            text += f"\nError: {report.error_message}\n"
        
        if report.screenshots:
            text += f"\n{len(report.screenshots)} screenshots attached."
        
        return text


class AutomationScheduler:
    """
    Manages scheduled execution of recordings.
    
    Features:
    - Multiple schedule types (once, hourly, daily, weekly, monthly, cron)
    - Persistent schedule storage
    - Email notifications
    - Runs in background
    """
    
    def __init__(
        self,
        storage_dir: Path,
        credential_manager: CredentialManager,
        recordings_dir: Path,
        email_config: Optional[EmailConfig] = None,
        on_run_complete: Optional[Callable[[Schedule, PlaybackReport], None]] = None
    ):
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.credential_manager = credential_manager
        self.recordings_dir = recordings_dir
        self.email_config = email_config
        self.on_run_complete = on_run_complete
        
        self._schedules_file = storage_dir / "schedules.json"
        self._schedules: Dict[str, Schedule] = {}
        
        # Email notifier
        self._notifier = EmailNotifier(email_config) if email_config else None
        
        # APScheduler setup
        jobstores = {
            'default': SQLAlchemyJobStore(url=f'sqlite:///{storage_dir / "jobs.db"}')
        }
        executors = {
            'default': ThreadPoolExecutor(max_workers=2)
        }
        
        self._scheduler = BackgroundScheduler(
            jobstores=jobstores,
            executors=executors,
            job_defaults={'coalesce': True, 'max_instances': 1}
        )
        
        self._load_schedules()
    
    def _load_schedules(self) -> None:
        """Load schedules from persistent storage."""
        if self._schedules_file.exists():
            try:
                data = json.loads(self._schedules_file.read_text())
                self._schedules = {
                    sid: Schedule.from_dict(sdata) 
                    for sid, sdata in data.items()
                }
                logger.info(f"Loaded {len(self._schedules)} schedules")
            except Exception as e:
                logger.error(f"Failed to load schedules: {e}")
                self._schedules = {}
    
    def _save_schedules(self) -> None:
        """Save schedules to persistent storage."""
        data = {sid: s.to_dict() for sid, s in self._schedules.items()}
        self._schedules_file.write_text(json.dumps(data, indent=2))
    
    def start(self) -> None:
        """Start the scheduler."""
        self._scheduler.start()
        
        # Re-add active schedules to APScheduler
        for schedule in self._schedules.values():
            if schedule.is_active:
                self._add_job(schedule)
        
        logger.info("Scheduler started")
    
    def stop(self) -> None:
        """Stop the scheduler."""
        self._scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
    
    def create_schedule(
        self,
        name: str,
        recording_id: str,
        recording_name: str,
        frequency: ScheduleFrequency,
        email_recipients: List[str],
        cron_expression: Optional[str] = None,
        speed_multiplier: float = 1.0,
        verify_visuals: bool = True,
        start_immediately: bool = True
    ) -> Schedule:
        """Create a new schedule."""
        import uuid
        
        schedule = Schedule(
            id=str(uuid.uuid4()),
            name=name,
            recording_id=recording_id,
            recording_name=recording_name,
            frequency=frequency,
            cron_expression=cron_expression,
            email_recipients=email_recipients,
            is_active=True,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            speed_multiplier=speed_multiplier,
            verify_visuals=verify_visuals
        )
        
        self._schedules[schedule.id] = schedule
        self._save_schedules()
        
        if start_immediately:
            self._add_job(schedule)
        
        logger.info(f"Schedule created: {name}")
        return schedule
    
    def update_schedule(self, schedule_id: str, **kwargs) -> Optional[Schedule]:
        """Update an existing schedule."""
        schedule = self._schedules.get(schedule_id)
        if not schedule:
            return None
        
        # Update fields
        for key, value in kwargs.items():
            if hasattr(schedule, key):
                setattr(schedule, key, value)
        
        schedule.updated_at = datetime.now()
        
        # Remove and re-add job if active
        self._remove_job(schedule_id)
        if schedule.is_active:
            self._add_job(schedule)
        
        self._save_schedules()
        return schedule
    
    def delete_schedule(self, schedule_id: str) -> bool:
        """Delete a schedule."""
        if schedule_id not in self._schedules:
            return False
        
        self._remove_job(schedule_id)
        del self._schedules[schedule_id]
        self._save_schedules()
        
        logger.info(f"Schedule deleted: {schedule_id}")
        return True
    
    def get_schedule(self, schedule_id: str) -> Optional[Schedule]:
        """Get a schedule by ID."""
        return self._schedules.get(schedule_id)
    
    def list_schedules(self) -> List[Schedule]:
        """List all schedules."""
        return list(self._schedules.values())
    
    def set_active(self, schedule_id: str, active: bool) -> bool:
        """Enable or disable a schedule."""
        schedule = self._schedules.get(schedule_id)
        if not schedule:
            return False
        
        schedule.is_active = active
        schedule.updated_at = datetime.now()
        
        if active:
            self._add_job(schedule)
        else:
            self._remove_job(schedule_id)
        
        self._save_schedules()
        return True
    
    def run_now(self, schedule_id: str) -> Optional[PlaybackReport]:
        """Run a schedule immediately."""
        schedule = self._schedules.get(schedule_id)
        if not schedule:
            return None
        
        return self._execute_schedule(schedule)
    
    def _add_job(self, schedule: Schedule) -> None:
        """Add schedule to APScheduler."""
        trigger = self._create_trigger(schedule)
        
        self._scheduler.add_job(
            self._execute_schedule,
            trigger=trigger,
            args=[schedule],
            id=schedule.id,
            name=schedule.name,
            replace_existing=True,
            misfire_grace_time=300
        )
        
        # Update next run time
        job = self._scheduler.get_job(schedule.id)
        if job:
            schedule.next_run = job.next_run_time
            self._save_schedules()
    
    def _remove_job(self, schedule_id: str) -> None:
        """Remove job from APScheduler."""
        try:
            self._scheduler.remove_job(schedule_id)
        except Exception:
            pass
    
    def _create_trigger(self, schedule: Schedule):
        """Create APScheduler trigger from schedule."""
        freq = schedule.frequency
        if freq == ScheduleFrequency.ONCE:
            return DateTrigger(run_date=datetime.now() + timedelta(minutes=1))
        elif freq == ScheduleFrequency.HOURLY:
            return IntervalTrigger(hours=1)
        elif freq == ScheduleFrequency.DAILY:
            return IntervalTrigger(days=1)
        elif freq == ScheduleFrequency.WEEKLY:
            return IntervalTrigger(weeks=1)
        elif freq == ScheduleFrequency.MONTHLY:
            return IntervalTrigger(days=30)
        elif freq == ScheduleFrequency.CUSTOM:
            return CronTrigger.from_crontab(schedule.cron_expression)
    
    def _execute_schedule(self, schedule: Schedule) -> PlaybackReport:
        """Execute a scheduled recording."""
        logger.info(f"Executing schedule: {schedule.name}")
        
        try:
            # Load recording
            recording_path = self.recordings_dir / f"{schedule.recording_id}.json"
            if not recording_path.exists():
                raise FileNotFoundError(f"Recording not found: {schedule.recording_id}")
            
            recording = Recording.load(recording_path)
            
            # Create playback engine
            playback_dir = self.storage_dir / "playback" / schedule.id
            engine = PlaybackEngine(
                credential_manager=self.credential_manager,
                storage_dir=playback_dir
            )
            
            # Execute
            report = engine.execute(
                recording=recording,
                speed_multiplier=schedule.speed_multiplier,
                verify_visuals=schedule.verify_visuals
            )
            
            # Update schedule stats
            schedule.last_run = datetime.now()
            schedule.run_count += 1
            if report.status == PlaybackStatus.COMPLETED:
                schedule.success_count += 1
            else:
                schedule.failure_count += 1
            
            # Update next run
            job = self._scheduler.get_job(schedule.id)
            if job:
                schedule.next_run = job.next_run_time
            
            self._save_schedules()
            
            # Send email notification
            if self._notifier and schedule.email_recipients:
                self._notifier.send_report(
                    recipients=schedule.email_recipients,
                    schedule_name=schedule.name,
                    report=report
                )
            
            # Callback
            if self.on_run_complete:
                self.on_run_complete(schedule, report)
            
            engine.close()
            return report
            
        except Exception as e:
            logger.error(f"Schedule execution failed: {e}")
            
            # Create error report
            report = PlaybackReport(
                recording_id=schedule.recording_id,
                recording_name=schedule.recording_name,
                status=PlaybackStatus.FAILED,
                started_at=time.time(),
                completed_at=time.time(),
                total_actions=0,
                completed_actions=0,
                failed_actions=0,
                screenshots=[],
                results=[],
                error_message=str(e)
            )
            
            # Update stats
            schedule.last_run = datetime.now()
            schedule.run_count += 1
            schedule.failure_count += 1
            self._save_schedules()
            
            # Send error notification
            if self._notifier and schedule.email_recipients:
                self._notifier.send_report(
                    recipients=schedule.email_recipients,
                    schedule_name=schedule.name,
                    report=report
                )
            
            return report
    
    def set_email_config(self, config: EmailConfig) -> None:
        """Update email configuration."""
        self.email_config = config
        self._notifier = EmailNotifier(config)
        
        # Save config
        config_path = self.storage_dir / "email_config.json"
        config_path.write_text(json.dumps(config.to_dict()))
    
    def get_email_config(self) -> Optional[EmailConfig]:
        """Get current email configuration."""
        return self.email_config

