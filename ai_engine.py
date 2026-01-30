"""
AI Engine for audit analysis using Anthropic Claude.
Provides screenshot analysis and executive summary generation.
Token-efficient: one API call for all screenshots + one for summary.
"""
import base64
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PIL import Image
import io

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class StepVerdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"
    SKIP = "skip"


@dataclass
class TimingData:
    recording_timestamp: float = 0.0
    playback_timestamp: float = 0.0
    playback_duration_ms: int = 0
    page_load_time_ms: Optional[int] = None


@dataclass
class StepAnalysis:
    step_index: int
    action_id: str
    action_type: str
    action_description: str
    verdict: StepVerdict = StepVerdict.SKIP
    match_score: float = 1.0
    changes_detected: List[str] = field(default_factory=list)
    errors_detected: List[str] = field(default_factory=list)
    data_extracted: Dict[str, Any] = field(default_factory=dict)
    ai_commentary: str = ""
    rec_screenshot_path: Optional[str] = None
    play_screenshot_path: Optional[str] = None
    timing: Optional[TimingData] = None
    has_screenshot: bool = False


@dataclass
class AuditFinding:
    severity: str  # "critical", "warning", "info"
    title: str
    description: str
    step_index: Optional[int] = None


@dataclass
class PerformanceMetrics:
    total_recording_time_s: float = 0.0
    total_playback_time_s: float = 0.0
    avg_step_duration_ms: float = 0.0
    slowest_step_index: int = 0
    slowest_step_duration_ms: int = 0
    page_load_times: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class AuditContext:
    recording_name: str = ""
    recording_id: str = ""
    purpose: str = ""
    verification_goal: str = ""
    url: Optional[str] = None
    total_steps: int = 0


@dataclass
class AuditReport:
    context: AuditContext = field(default_factory=AuditContext)
    step_analyses: List[StepAnalysis] = field(default_factory=list)
    findings: List[AuditFinding] = field(default_factory=list)
    performance: PerformanceMetrics = field(default_factory=PerformanceMetrics)
    executive_summary: str = ""
    screenshot_analysis: str = ""
    overall_score: int = 100
    generated_at: str = ""
    ai_model_used: str = ""
    total_api_calls: int = 0
    estimated_cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# AI Engine
# ---------------------------------------------------------------------------

class AIEngine:
    """Core AI module that interfaces with Anthropic Claude for audit analysis."""

    MODEL = "claude-sonnet-4-20250514"

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._client = None
        self._image_cache: Dict[str, str] = {}  # path -> base64
        self._api_calls = 0
        self._input_tokens = 0
        self._output_tokens = 0

    # -- lazy init ----------------------------------------------------------

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    # -- image helpers ------------------------------------------------------

    def _encode_image(self, path: str) -> str:
        if path in self._image_cache:
            return self._image_cache[path]

        img = Image.open(path)
        # Resize to save tokens — max 1024px on longest side
        max_dim = 1024
        if max(img.size) > max_dim:
            ratio = max_dim / max(img.size)
            new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
            img = img.resize(new_size, Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        self._image_cache[path] = b64
        return b64

    # -- API call -----------------------------------------------------------

    def _call_api_text(self, system: str, messages: list, max_tokens: int = 2048) -> str:
        """Call Claude and return raw text (not JSON)."""
        client = self._get_client()
        last_err = None

        for attempt in range(3):
            try:
                resp = client.messages.create(
                    model=self.MODEL,
                    max_tokens=max_tokens,
                    system=system,
                    messages=messages,
                )
                self._api_calls += 1
                self._input_tokens += resp.usage.input_tokens
                self._output_tokens += resp.usage.output_tokens
                return resp.content[0].text.strip()

            except Exception as e:
                last_err = e
                wait = (2 ** attempt) * 1.0
                logger.warning(f"API call failed (attempt {attempt+1}): {e}, retrying in {wait}s")
                time.sleep(wait)

        raise RuntimeError(f"API call failed after 3 retries: {last_err}")

    # -- screenshot analysis (single API call) ------------------------------

    def analyze_screenshots(
        self,
        screenshot_steps: List[dict],
        context: AuditContext,
        performance: PerformanceMetrics,
    ) -> str:
        """
        Analyze all playback screenshots in a single API call.
        Returns a nicely formatted text analysis (not JSON).

        Each entry in screenshot_steps: {index, action_type, description, play_screenshot}
        """
        if not screenshot_steps:
            return "No screenshots were captured during this playback run."

        # Build message content with all screenshots
        content = []
        content.append({
            "type": "text",
            "text": (
                f"You are reviewing screenshots from an automated playback of: {context.recording_name}\n"
                f"URL: {context.url or 'N/A'}\n"
                f"Purpose: {context.purpose or 'General automation audit'}\n"
                f"Verification goal: {context.verification_goal or 'Verify correct execution'}\n\n"
                f"Total actions in recording: {context.total_steps}\n"
                f"Total playback time: {performance.total_playback_time_s:.1f}s\n\n"
                f"Below are the {len(screenshot_steps)} screenshots captured during playback. "
                f"For each screenshot, I've noted which step it came from and what action was performed.\n\n"
                "Please analyze each screenshot and provide:\n"
                "- What is visible on screen\n"
                "- Any data, text, or important information shown\n"
                "- Any errors, warnings, or issues visible\n"
                "- Whether the page/application state looks correct\n\n"
                "Write your analysis in clear, readable prose organized by screenshot. "
                "Use section headers for each screenshot."
            )
        })

        for step in screenshot_steps:
            content.append({
                "type": "text",
                "text": f"\n--- Screenshot from Step {step['index'] + 1}: {step['action_type']} — {step['description']} ---"
            })
            b64 = self._encode_image(step["play_screenshot"])
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": b64}
            })

        system = (
            "You are a senior QA analyst reviewing automated test screenshots. "
            "Write clear, professional analysis in readable prose. No JSON. No code blocks. "
            "Use markdown-style headers (##) to organize by screenshot. "
            "Focus on what you see: page content, data displayed, any errors or anomalies, "
            "and whether the application appears to be functioning correctly."
        )

        messages = [{"role": "user", "content": content}]
        return self._call_api_text(system, messages, max_tokens=2048)

    # -- executive summary (single API call) --------------------------------

    def generate_executive_summary(
        self,
        context: AuditContext,
        performance: PerformanceMetrics,
        screenshot_analysis: str,
        total_actions: int,
        completed_actions: int,
        failed_actions: int,
    ) -> str:
        """
        Generate a readable executive summary of the entire recording workflow.
        Returns formatted text, not JSON.
        """
        # Build timing breakdown
        timing_info = (
            f"Recording time: {performance.total_recording_time_s:.1f}s\n"
            f"Playback time: {performance.total_playback_time_s:.1f}s\n"
            f"Average step duration: {performance.avg_step_duration_ms:.0f}ms\n"
            f"Slowest step: #{performance.slowest_step_index + 1} ({performance.slowest_step_duration_ms}ms)\n"
        )
        if performance.page_load_times:
            timing_info += "Page load times:\n"
            for pl in performance.page_load_times:
                timing_info += f"  Step {pl.get('step', '?')}: {pl.get('time_ms', 0)}ms\n"

        system = (
            "You are a senior QA analyst writing an executive summary for a stakeholder. "
            "Write in clear, professional prose. No JSON. No code blocks. "
            "Include a brief overall assessment, key observations from the screenshots, "
            "performance highlights, and any concerns. Keep it concise (3-5 paragraphs). "
            "End with a one-line overall verdict."
        )

        messages = [{
            "role": "user",
            "content": (
                f"Recording: {context.recording_name}\n"
                f"URL: {context.url or 'N/A'}\n"
                f"Purpose: {context.purpose or 'General automation test'}\n"
                f"Goal: {context.verification_goal or 'Verify correct execution'}\n\n"
                f"Execution Results:\n"
                f"  Total actions: {total_actions}\n"
                f"  Completed: {completed_actions}\n"
                f"  Failed: {failed_actions}\n\n"
                f"Timing:\n{timing_info}\n"
                f"Screenshot Analysis:\n{screenshot_analysis}\n\n"
                "Write an executive summary covering: overall assessment, "
                "key findings from screenshots, performance analysis, and any concerns."
            ),
        }]

        return self._call_api_text(system, messages, max_tokens=1024)

    # -- cost tracking ------------------------------------------------------

    @property
    def total_api_calls(self) -> int:
        return self._api_calls

    @property
    def estimated_cost_usd(self) -> float:
        # Approximate pricing for Sonnet: $3/M input, $15/M output
        return (self._input_tokens * 3 + self._output_tokens * 15) / 1_000_000

    @property
    def ai_model_used(self) -> str:
        return self.MODEL
