"""
Database models for persistent storage.
Stores recordings, schedules, run history, and settings.
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from sqlalchemy import (
    create_engine, Column, Integer, String, Text, DateTime, 
    Boolean, Float, ForeignKey, JSON
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session, make_transient

Base = declarative_base()


class RecordingModel(Base):
    """Stored recording metadata."""
    __tablename__ = "recordings"
    
    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    url = Column(String(2048))
    action_count = Column(Integer, default=0)
    file_path = Column(String(1024))  # Path to JSON file with full recording
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Playback settings
    speed_multiplier = Column(Float, default=1.0)
    verify_screenshots = Column(Boolean, default=True)

    # Audit metadata
    audit_purpose = Column(Text)
    audit_verification_goal = Column(Text)
    email_recipients = Column(JSON)  # List of email addresses for audit reports
    
    # Relationships
    schedules = relationship("ScheduleModel", back_populates="recording", cascade="all, delete-orphan")
    runs = relationship("RunModel", back_populates="recording", cascade="all, delete-orphan")


class ScheduleModel(Base):
    """Scheduled execution configuration."""
    __tablename__ = "schedules"
    
    id = Column(String(36), primary_key=True)
    recording_id = Column(String(36), ForeignKey("recordings.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    frequency = Column(String(50), nullable=False)  # once, hourly, daily, weekly, monthly, custom
    cron_expression = Column(String(100))
    email_recipients = Column(JSON)  # List of email addresses
    is_active = Column(Boolean, default=True)
    timezone = Column(String(50), default="UTC")
    
    # Stats
    last_run = Column(DateTime)
    next_run = Column(DateTime)
    run_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    failure_count = Column(Integer, default=0)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    recording = relationship("RecordingModel", back_populates="schedules")
    runs = relationship("RunModel", back_populates="schedule", cascade="all, delete-orphan")


class RunModel(Base):
    """Execution history record."""
    __tablename__ = "runs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    recording_id = Column(String(36), ForeignKey("recordings.id", ondelete="CASCADE"), nullable=False)
    schedule_id = Column(String(36), ForeignKey("schedules.id", ondelete="SET NULL"))
    
    status = Column(String(50), nullable=False)  # running, completed, failed, aborted
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
    duration_seconds = Column(Float)
    
    total_actions = Column(Integer, default=0)
    completed_actions = Column(Integer, default=0)
    failed_actions = Column(Integer, default=0)
    
    error_message = Column(Text)
    screenshots = Column(JSON)  # List of screenshot paths
    audit_report_path = Column(String(1024))

    # Relationships
    recording = relationship("RecordingModel", back_populates="runs")
    schedule = relationship("ScheduleModel", back_populates="runs")


class CredentialModel(Base):
    """Stored credential reference (not the actual password)."""
    __tablename__ = "credentials"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    username = Column(String(255))
    url = Column(String(2048))
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SettingsModel(Base):
    """Application settings."""
    __tablename__ = "settings"
    
    key = Column(String(255), primary_key=True)
    value = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Database:
    """Database manager."""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.engine = create_engine(f"sqlite:///{db_path}", echo=False)
        Base.metadata.create_all(self.engine)
        
        self.SessionLocal = sessionmaker(bind=self.engine)
    
    def get_session(self) -> Session:
        """Get database session."""
        return self.SessionLocal()
    
    # Recording operations
    def save_recording(
        self,
        id: str,
        name: str,
        description: str,
        url: str,
        action_count: int,
        file_path: str,
        email_recipients: Optional[list] = None
    ) -> RecordingModel:
        """Save or update recording metadata."""
        with self.get_session() as session:
            recording = session.query(RecordingModel).filter_by(id=id).first()

            if recording:
                recording.name = name
                recording.description = description
                recording.url = url
                recording.action_count = action_count
                recording.file_path = file_path
                recording.updated_at = datetime.utcnow()
                if email_recipients is not None:
                    recording.email_recipients = email_recipients
            else:
                recording = RecordingModel(
                    id=id,
                    name=name,
                    description=description,
                    url=url,
                    action_count=action_count,
                    file_path=file_path,
                    email_recipients=email_recipients
                )
                session.add(recording)
            
            session.commit()
            session.refresh(recording)
            return recording
    
    def get_recording(self, id: str) -> Optional[RecordingModel]:
        """Get recording by ID."""
        with self.get_session() as session:
            obj = session.query(RecordingModel).filter_by(id=id).first()
            if obj:
                session.expunge(obj)
                make_transient(obj)
            return obj

    def list_recordings(self) -> List[RecordingModel]:
        """List all recordings."""
        with self.get_session() as session:
            results = session.query(RecordingModel).order_by(RecordingModel.updated_at.desc()).all()
            for obj in results:
                session.expunge(obj)
                make_transient(obj)
            return results
    
    def delete_recording(self, id: str) -> bool:
        """Delete recording."""
        with self.get_session() as session:
            recording = session.query(RecordingModel).filter_by(id=id).first()
            if recording:
                session.delete(recording)
                session.commit()
                return True
            return False
    
    # Run operations
    def save_run(
        self,
        recording_id: str,
        schedule_id: Optional[str],
        status: str,
        total_actions: int,
        completed_actions: int,
        failed_actions: int,
        duration_seconds: float,
        error_message: Optional[str],
        screenshots: List[str]
    ) -> RunModel:
        """Save run record."""
        with self.get_session() as session:
            run = RunModel(
                recording_id=recording_id,
                schedule_id=schedule_id,
                status=status,
                total_actions=total_actions,
                completed_actions=completed_actions,
                failed_actions=failed_actions,
                duration_seconds=duration_seconds,
                error_message=error_message,
                screenshots=screenshots,
                completed_at=datetime.utcnow()
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            return run
    
    def list_runs(self, recording_id: Optional[str] = None, limit: int = 50) -> List[RunModel]:
        """List runs with optional filtering."""
        with self.get_session() as session:
            query = session.query(RunModel)
            if recording_id:
                query = query.filter_by(recording_id=recording_id)
            results = query.order_by(RunModel.started_at.desc()).limit(limit).all()
            for obj in results:
                session.expunge(obj)
                make_transient(obj)
            return results
    
    # Settings operations
    def get_setting(self, key: str, default: Any = None) -> Any:
        """Get setting value."""
        with self.get_session() as session:
            setting = session.query(SettingsModel).filter_by(key=key).first()
            if setting:
                return json.loads(setting.value)
            return default
    
    def set_setting(self, key: str, value: Any) -> None:
        """Set setting value."""
        with self.get_session() as session:
            setting = session.query(SettingsModel).filter_by(key=key).first()
            if setting:
                setting.value = json.dumps(value)
            else:
                setting = SettingsModel(key=key, value=json.dumps(value))
                session.add(setting)
            session.commit()
