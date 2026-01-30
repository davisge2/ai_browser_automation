"""
HTML Report Generator for AI audit reports.
Produces a self-contained HTML file with inline screenshots, timing data,
and readable AI analysis. Designed to also work as an embedded email body.
"""
import base64
import logging
import re
from pathlib import Path

from ai_engine import AuditReport, StepAnalysis

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Generates a self-contained HTML audit report."""

    @staticmethod
    def _encode_image_file(path: str) -> str:
        try:
            data = Path(path).read_bytes()
            return base64.b64encode(data).decode("utf-8")
        except Exception:
            return ""

    @staticmethod
    def _md_to_html(text: str) -> str:
        """Minimal markdown-to-HTML: headers and paragraphs."""
        lines = text.split("\n")
        html_parts = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("## "):
                html_parts.append(f'<h3 style="color:#e0e0e0;margin:18px 0 8px;">{stripped[3:]}</h3>')
            elif stripped.startswith("# "):
                html_parts.append(f'<h2 style="color:#fff;margin:20px 0 10px;">{stripped[2:]}</h2>')
            elif stripped.startswith("- "):
                html_parts.append(f'<li style="color:#ccc;margin:2px 0 2px 20px;">{stripped[2:]}</li>')
            elif stripped:
                html_parts.append(f'<p style="color:#ccc;margin:6px 0;">{stripped}</p>')
        return "\n".join(html_parts)

    def generate(self, report: AuditReport, output_path: str) -> str:
        html = self._build_html(report)
        Path(output_path).write_text(html, encoding="utf-8")
        logger.info(f"Report generated: {output_path}")
        return output_path

    def get_html(self, report: AuditReport) -> str:
        """Return the HTML string without writing to disk."""
        return self._build_html(report)

    def _build_html(self, r: AuditReport) -> str:
        perf = r.performance

        # Performance rows
        perf_rows = f'''
            <tr><td style="padding:10px 16px;color:#aaa;">Total Recording Time</td>
                <td style="padding:10px 16px;color:#fff;font-weight:600;">{perf.total_recording_time_s:.1f}s</td></tr>
            <tr><td style="padding:10px 16px;color:#aaa;">Total Playback Time</td>
                <td style="padding:10px 16px;color:#fff;font-weight:600;">{perf.total_playback_time_s:.1f}s</td></tr>
            <tr><td style="padding:10px 16px;color:#aaa;">Avg Step Duration</td>
                <td style="padding:10px 16px;color:#fff;font-weight:600;">{perf.avg_step_duration_ms:.0f}ms</td></tr>
            <tr><td style="padding:10px 16px;color:#aaa;">Slowest Step</td>
                <td style="padding:10px 16px;color:#fff;font-weight:600;">#{perf.slowest_step_index + 1} ({perf.slowest_step_duration_ms}ms)</td></tr>'''
        for pl in perf.page_load_times:
            perf_rows += f'''
            <tr><td style="padding:10px 16px;color:#aaa;">Page Load (Step {pl.get("step", "?")})</td>
                <td style="padding:10px 16px;color:#fff;font-weight:600;">{pl.get("time_ms", 0)}ms</td></tr>'''

        # Screenshots section — only steps that have playback screenshots
        screenshots_html = ""
        for sa in r.step_analyses:
            if not sa.play_screenshot_path:
                continue
            b64 = self._encode_image_file(sa.play_screenshot_path)
            if not b64:
                continue
            timing_str = ""
            if sa.timing:
                timing_str = f'{sa.timing.playback_duration_ms}ms'
                if sa.timing.page_load_time_ms is not None:
                    timing_str += f' | Page load: {sa.timing.page_load_time_ms}ms'

            screenshots_html += f'''
            <div style="background:#1a1a2e;border-radius:8px;padding:16px;margin:12px 0;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
                    <span style="color:#fff;font-weight:600;">Step {sa.step_index + 1}: {sa.action_type}</span>
                    {f'<span style="color:#888;font-size:13px;">{timing_str}</span>' if timing_str else ''}
                </div>
                <p style="color:#999;font-size:13px;margin:0 0 10px;">{sa.action_description}</p>
                <img src="data:image/png;base64,{b64}" style="max-width:100%;border-radius:4px;border:1px solid #333;">
            </div>'''

        # Action log table
        log_rows = ""
        for sa in r.step_analyses:
            dur = f"{sa.timing.playback_duration_ms}ms" if sa.timing else "-"
            pl = ""
            if sa.timing and sa.timing.page_load_time_ms is not None:
                pl = f"{sa.timing.page_load_time_ms}ms"
            ss_icon = "📸" if sa.play_screenshot_path else ""
            log_rows += f'''<tr>
                <td style="padding:6px 12px;">{sa.step_index + 1}</td>
                <td style="padding:6px 12px;">{sa.action_type}</td>
                <td style="padding:6px 12px;color:#aaa;">{sa.action_description}</td>
                <td style="padding:6px 12px;">{dur}</td>
                <td style="padding:6px 12px;">{pl}</td>
                <td style="padding:6px 12px;text-align:center;">{ss_icon}</td>
            </tr>'''

        # Convert markdown analysis to HTML
        summary_html = self._md_to_html(r.executive_summary) if r.executive_summary else '<p style="color:#888;">No summary generated.</p>'
        screenshot_analysis_html = self._md_to_html(r.screenshot_analysis) if r.screenshot_analysis else ''

        # Stats
        total = r.context.total_steps
        completed = sum(1 for s in r.step_analyses if s.timing and s.timing.playback_duration_ms > 0)
        ss_count = sum(1 for s in r.step_analyses if s.play_screenshot_path)

        html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Audit Report — {r.context.recording_name}</title>
</head>
<body style="margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0f0f1a;color:#e0e0e0;line-height:1.6;">

<!-- Header -->
<div style="background:linear-gradient(135deg,#1a1a3e,#2d1b69);padding:40px 20px;text-align:center;">
    <h1 style="color:#fff;font-size:26px;margin:0;">AI Audit Report</h1>
    <p style="color:#aaa;margin:8px 0 0;">{r.context.recording_name}</p>
    {f'<p style="color:#888;font-size:14px;margin:4px 0;">{r.context.url}</p>' if r.context.url else ''}
    <p style="color:#666;font-size:12px;margin:12px 0 0;">{r.generated_at} | {r.ai_model_used} | {r.total_api_calls} API calls | ${r.estimated_cost_usd:.4f}</p>
</div>

<div style="max-width:900px;margin:0 auto;padding:20px;">

<!-- Quick Stats -->
<div style="display:flex;gap:12px;margin:24px 0;flex-wrap:wrap;">
    <div style="flex:1;min-width:120px;text-align:center;padding:16px;background:#1e1e2e;border-radius:8px;">
        <div style="font-size:28px;font-weight:bold;color:#fff;">{total}</div>
        <div style="color:#888;font-size:13px;">Total Actions</div>
    </div>
    <div style="flex:1;min-width:120px;text-align:center;padding:16px;background:#1e1e2e;border-radius:8px;">
        <div style="font-size:28px;font-weight:bold;color:#22c55e;">{completed}</div>
        <div style="color:#888;font-size:13px;">Completed</div>
    </div>
    <div style="flex:1;min-width:120px;text-align:center;padding:16px;background:#1e1e2e;border-radius:8px;">
        <div style="font-size:28px;font-weight:bold;color:#7c3aed;">{ss_count}</div>
        <div style="color:#888;font-size:13px;">Screenshots</div>
    </div>
    <div style="flex:1;min-width:120px;text-align:center;padding:16px;background:#1e1e2e;border-radius:8px;">
        <div style="font-size:28px;font-weight:bold;color:#3b82f6;">{perf.total_playback_time_s:.1f}s</div>
        <div style="color:#888;font-size:13px;">Playback Time</div>
    </div>
</div>

<!-- Executive Summary -->
<div style="background:#1e1e2e;border-radius:8px;padding:24px;margin:24px 0;">
    <h2 style="color:#fff;margin:0 0 16px;font-size:20px;">Executive Summary</h2>
    {summary_html}
</div>

{f"""<!-- Audit Context -->
<div style="background:#1e1e2e;border-radius:8px;padding:24px;margin:24px 0;">
    <h2 style="color:#fff;margin:0 0 12px;font-size:20px;">Audit Context</h2>
    <p style="color:#aaa;"><strong style="color:#ccc;">Purpose:</strong> {r.context.purpose}</p>
    <p style="color:#aaa;"><strong style="color:#ccc;">Verification Goal:</strong> {r.context.verification_goal}</p>
</div>""" if r.context.purpose or r.context.verification_goal else ""}

<!-- Performance & Timing -->
<div style="background:#1e1e2e;border-radius:8px;padding:24px;margin:24px 0;">
    <h2 style="color:#fff;margin:0 0 16px;font-size:20px;">Performance & Timing</h2>
    <table style="width:100%;border-collapse:collapse;">
        {perf_rows}
    </table>
</div>

<!-- Screenshot Analysis -->
{f"""<div style="background:#1e1e2e;border-radius:8px;padding:24px;margin:24px 0;">
    <h2 style="color:#fff;margin:0 0 16px;font-size:20px;">Screenshot Analysis</h2>
    {screenshot_analysis_html}
</div>""" if screenshot_analysis_html else ""}

<!-- Screenshots -->
{f"""<div style="margin:24px 0;">
    <h2 style="color:#fff;margin:0 0 16px;font-size:20px;">Captured Screenshots</h2>
    {screenshots_html}
</div>""" if screenshots_html else ""}

<!-- Action Log -->
<div style="background:#1e1e2e;border-radius:8px;padding:24px;margin:24px 0;">
    <h2 style="color:#fff;margin:0 0 16px;font-size:20px;">Action Log</h2>
    <div style="overflow-x:auto;">
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead><tr style="border-bottom:2px solid #333;">
            <th style="padding:8px 12px;text-align:left;color:#888;font-size:11px;text-transform:uppercase;">#</th>
            <th style="padding:8px 12px;text-align:left;color:#888;font-size:11px;text-transform:uppercase;">Type</th>
            <th style="padding:8px 12px;text-align:left;color:#888;font-size:11px;text-transform:uppercase;">Description</th>
            <th style="padding:8px 12px;text-align:left;color:#888;font-size:11px;text-transform:uppercase;">Duration</th>
            <th style="padding:8px 12px;text-align:left;color:#888;font-size:11px;text-transform:uppercase;">Page Load</th>
            <th style="padding:8px 12px;text-align:center;color:#888;font-size:11px;text-transform:uppercase;">SS</th>
        </tr></thead>
        <tbody style="color:#ccc;">{log_rows}</tbody>
    </table>
    </div>
</div>

<!-- Footer -->
<div style="text-align:center;padding:24px;color:#555;font-size:12px;border-top:1px solid #2a2a3a;margin-top:24px;">
    Desktop Automation Recorder — AI Audit System
</div>

</div>
</body>
</html>'''
        return html
