"""PDF report generation for job diagnostic results."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, mm
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, KeepTogether, HRFlowable,
)
from reportlab.platypus.flowables import Flowable

from app.config import APP_NAME, APP_VERSION, PDF_REPORTS_DIR
from app.database.job_repo import get_job

logger = logging.getLogger(__name__)

STEP_LABELS = {
    "log_reader": "Log Reader",
    "set_batch_date": "Set Batch Date",
    "sql_executer": "SQL Executer",
    "cte_parser": "CTE Parser",
    "cte_executer": "CTE Executer",
    "sql_diagnostics": "SQL Diagnostics",
}

STATUS_COLORS = {
    "completed": "#10b981",
    "failed": "#ef4444",
    "running": "#3b82f6",
    "pending": "#94a3b8",
}

STEP_LABELS = {
    "log_reader": "Log Reader",
    "set_batch_date": "Set Batch Date",
    "sql_executer": "SQL Executer",
    "cte_parser": "CTE Parser",
    "cte_executer": "CTE Executer",
    "sql_diagnostics": "SQL Diagnostics",
}

ACCENT = colors.HexColor("#2563eb")
DARK = colors.HexColor("#0f172a")
MUTED = colors.HexColor("#64748b")
BORDER = colors.HexColor("#e2e8f0")
BG_STRIPE = colors.HexColor("#f8fafc")
DANGER_BG = colors.HexColor("#fef2f2")
SUCCESS_BG = colors.HexColor("#f0fdf4")
SECTION_BG = colors.HexColor("#eff6ff")


def generate_job_report(job_id: str) -> bytes:
    job = get_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    metadata = _load_metadata(job)

    filename = f"report_{job_id[:8]}.pdf"
    filepath = PDF_REPORTS_DIR / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)

    buffer = filepath.open("wb")
    doc = SimpleDocTemplate(
        str(filepath),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title=f"Diagnostic Report — {job.get('scenario_name', 'Unknown')}",
        author=APP_NAME,
    )

    story = _build_story(job, metadata)
    doc.build(story)
    buffer.close()

    pdf_bytes = filepath.read_bytes()
    logger.info("PDF report generated: %s (%d bytes)", filename, len(pdf_bytes))
    return pdf_bytes


def _build_story(job: dict, metadata: dict = None) -> list:
    styles = _create_styles()
    story = []

    story.extend(_header_section(styles))
    story.append(Spacer(1, 6 * mm))
    story.extend(_summary_section(job, metadata, styles))
    story.append(Spacer(1, 8 * mm))
    story.extend(_steps_section(job, styles))

    cte_results = _parse_json(job.get("cte_results_json"))
    if cte_results:
        story.append(Spacer(1, 8 * mm))
        story.extend(_cte_waterfall_section(cte_results, styles))

    diagnostic_results = _parse_json(job.get("result_json"))
    if diagnostic_results:
        story.append(Spacer(1, 8 * mm))
        story.extend(_root_cause_section(job, diagnostic_results, styles))

    story.append(Spacer(1, 10 * mm))
    story.extend(_footer_section(styles))
    return story


def _create_styles() -> dict:
    base = getSampleStyleSheet()

    styles = {
        "h1": ParagraphStyle(
            "ReportH1", parent=base["Heading1"],
            fontSize=20, leading=24, textColor=DARK, spaceAfter=4,
            fontName="Helvetica-Bold",
        ),
        "h2": ParagraphStyle(
            "ReportH2", parent=base["Heading2"],
            fontSize=13, leading=16, textColor=ACCENT, spaceBefore=6, spaceAfter=4,
            fontName="Helvetica-Bold",
        ),
        "h3": ParagraphStyle(
            "ReportH3", parent=base["Heading3"],
            fontSize=10, leading=13, textColor=DARK, spaceBefore=4, spaceAfter=2,
            fontName="Helvetica-Bold",
        ),
        "body": ParagraphStyle(
            "ReportBody", parent=base["Normal"],
            fontSize=8.5, leading=12, textColor=DARK, spaceAfter=3,
            fontName="Helvetica",
        ),
        "body_small": ParagraphStyle(
            "ReportBodySmall", parent=base["Normal"],
            fontSize=7.5, leading=10, textColor=MUTED, spaceAfter=2,
            fontName="Helvetica",
        ),
        "mono": ParagraphStyle(
            "ReportMono", parent=base["Code"],
            fontSize=7, leading=9, textColor=DARK, spaceAfter=3,
            fontName="Courier", backColor=colors.HexColor("#f1f5f9"),
            borderPadding=4,
        ),
        "mono_small": ParagraphStyle(
            "ReportMonoSmall", parent=base["Code"],
            fontSize=6.5, leading=8, textColor=DARK, spaceAfter=1,
            fontName="Courier", backColor=colors.HexColor("#f8fafc"),
            borderPadding=3,
        ),
        "label": ParagraphStyle(
            "ReportLabel", parent=base["Normal"],
            fontSize=8, leading=11, textColor=MUTED, spaceAfter=1,
            fontName="Helvetica-Bold",
        ),
        "value": ParagraphStyle(
            "ReportValue", parent=base["Normal"],
            fontSize=9, leading=12, textColor=DARK, spaceAfter=4,
            fontName="Helvetica",
        ),
        "badge_ok": ParagraphStyle(
            "BadgeOK", parent=base["Normal"],
            fontSize=7.5, leading=10, textColor=colors.HexColor("#10b981"),
            fontName="Helvetica-Bold",
        ),
        "badge_fail": ParagraphStyle(
            "BadgeFail", parent=base["Normal"],
            fontSize=7.5, leading=10, textColor=colors.HexColor("#ef4444"),
            fontName="Helvetica-Bold",
        ),
        "footer": ParagraphStyle(
            "Footer", parent=base["Normal"],
            fontSize=7, leading=9, textColor=MUTED, alignment=TA_CENTER,
        ),
    }
    return styles


def _header_section(styles: dict) -> list:
    elements = []
    elements.append(HRFlowable(width="100%", thickness=2, color=ACCENT, spaceAfter=6))
    elements.append(Paragraph(APP_NAME, styles["h1"]))
    elements.append(Paragraph(f"Diagnostic Report  ·  v{APP_VERSION}", styles["body_small"]))
    elements.append(Paragraph(f"Generated: {_format_dt(datetime.now(timezone.utc))}", styles["body_small"]))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceBefore=6, spaceAfter=6))
    return elements


def _summary_section(job: dict, metadata: dict = None, styles: dict = None) -> list:
    elements = []
    elements.append(Paragraph("Job Summary", styles["h2"]))

    data = [
        ["Job ID", job.get("job_id", "")[:16] + "…"],
        ["Scenario", job.get("scenario_name") or "Unknown"],
        ["Batch Date", job.get("batch_date") or "N/A"],
        ["Status", job.get("status", "unknown").upper()],
        ["Alerts Generated", "Yes" if job.get("alerts_generated") else "No"],
        ["Log File", job.get("log_filename", "N/A")],
        ["Started", _format_dt_str(job.get("started_at"))],
        ["Completed", _format_dt_str(job.get("completed_at"))],
    ]

    if metadata:
        if metadata.get("scnro_id"):
            data.append(["Scenario ID", str(metadata["scnro_id"])])
        if metadata.get("tshld_set_id"):
            data.append(["Threshold Set ID", str(metadata["tshld_set_id"])])
        if metadata.get("current_business_date"):
            data.append(["Business Date", str(metadata["current_business_date"])])

    if job.get("error_message"):
        data.append(["Error", job["error_message"]])

    table = Table(data, colWidths=[100, 380], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
        ("TEXTCOLOR", (1, 0), (1, -1), DARK),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    elements.append(table)

    root_cause = job.get("root_cause")
    if root_cause:
        elements.append(Spacer(1, 4 * mm))
        elements.append(Paragraph("Root Cause Summary", styles["h3"]))
        elements.append(Paragraph(root_cause[:500], styles["mono"]))

    return elements


def _steps_section(job: dict, styles: dict) -> list:
    elements = []
    elements.append(Paragraph("Pipeline Steps", styles["h2"]))

    steps = job.get("steps", [])
    if not steps:
        elements.append(Paragraph("No step data available.", styles["body_small"]))
        return elements

    header = [Paragraph("<b>Step</b>", styles["body"]),
              Paragraph("<b>Status</b>", styles["body"]),
              Paragraph("<b>Started</b>", styles["body"]),
              Paragraph("<b>Completed</b>", styles["body"])]
    rows = [header]

    for step in steps:
        name = STEP_LABELS.get(step.get("step_name"), step.get("step_name", ""))
        status = step.get("status", "pending")
        status_color = STATUS_COLORS.get(status, "#64748b")
        rows.append([
            Paragraph(name, styles["body"]),
            Paragraph(f"<font color='{status_color}'>● {status.upper()}</font>", styles["body"]),
            Paragraph(_format_dt_str(step.get("started_at")) or "—", styles["body_small"]),
            Paragraph(_format_dt_str(step.get("completed_at")) or "—", styles["body_small"]),
        ])

        output = step.get("output")
        if output and len(output) > 10:
            rows.append([
                Paragraph("", styles["body"]),
                Paragraph(
                    f"<i>Output: {output[:300]}{'...' if len(output) > 300 else ''}</i>",
                    styles["body_small"],
                ),
                "", "",
            ])
        error = step.get("error")
        if error:
            rows.append([
                Paragraph("", styles["body"]),
                Paragraph(
                    f"<font color='#ef4444'><b>Error:</b> {error[:300]}{'...' if len(error) > 300 else ''}</font>",
                    styles["body_small"],
                ),
                "", "",
            ])

    col_widths = [90, 80, 110, 110]
    table = Table(rows, colWidths=col_widths, hAlign="LEFT")
    style_cmds = [
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ("TEXTCOLOR", (0, 0), (-1, 0), DARK),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.3, BORDER),
    ]
    for i in range(1, len(rows)):
        if i % 2 == 0:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), BG_STRIPE))
    table.setStyle(TableStyle(style_cmds))
    elements.append(table)

    return elements


def _cte_waterfall_section(cte_results: list, styles: dict) -> list:
    elements = []
    elements.append(Paragraph("CTE Execution Waterfall", styles["h2"]))

    header = [Paragraph("<b>CTE Name</b>", styles["body"]),
              Paragraph("<b>Rows</b>", styles["body"]),
              Paragraph("<b>Status</b>", styles["body"])]
    rows = [header]

    for cte in cte_results:
        name = cte.get("name", "?")
        row_count = cte.get("rows", 0)
        status = cte.get("status", "unknown")
        passed = status == "ok"
        status_text = f"✓ {row_count}" if passed else f"✗ {row_count}"
        color_hex = "#10b981" if passed else "#ef4444"
        rows.append([
            Paragraph(name, styles["body"]),
            Paragraph(str(row_count), styles["body"]),
            Paragraph(f"<font color='{color_hex}'>{status_text}</font>", styles["body"]),
        ])

    col_widths = [200, 60, 70]
    table = Table(rows, colWidths=col_widths, hAlign="LEFT")
    style_cmds = [
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (-1, 0), SECTION_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), ACCENT),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.3, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i in range(1, len(rows)):
        if i % 2 == 0:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), BG_STRIPE))
    table.setStyle(TableStyle(style_cmds))
    elements.append(table)

    return elements


def _root_cause_section(job: dict, results: list, styles: dict) -> list:
    elements = []
    elements.append(Paragraph("Root Cause Analysis", styles["h2"]))

    root_cause = job.get("root_cause")
    if root_cause:
        elements.append(Paragraph(f"<b>Summary:</b> {root_cause[:400]}", styles["body"]))
        elements.append(Spacer(1, 2 * mm))

    ai_recommendation = job.get("ai_recommendation")
    if ai_recommendation:
        rec_html = ai_recommendation.replace("\n", "<br/>")
        elements.append(Paragraph(f"<b>AI Recommendation:</b><br/>{rec_html}", styles["body"]))
        elements.append(Spacer(1, 3 * mm))

    for result in results:
        cte_name = result.get("cte_name", "Unknown CTE")
        failure_type = result.get("failure_type", "unknown")
        condition = result.get("failure_condition", "")
        cause = result.get("likely_cause", "")
        line = result.get("condition_line_number")

        header_text = f"<b>{cte_name}</b>  —  <font color='#ef4444'>{failure_type.upper()}</font>"
        if line:
            header_text += f"  (Line {line})"

        t_data = [[Paragraph(header_text, styles["h3"])]]
        t = Table(t_data, colWidths=[500], hAlign="LEFT")
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), DANGER_BG),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ROUNDEDCORNERS", [3, 3, 3, 3]),
        ]))
        elements.append(Spacer(1, 2 * mm))
        elements.append(t)

        if condition:
            elements.append(Paragraph("<b>Failing Condition:</b>", styles["label"]))
            elements.append(Paragraph(condition[:400], styles["mono"]))
        if cause:
            elements.append(Paragraph(f"<b>Likely Cause:</b> {cause[:300]}", styles["body"]))

        verification = result.get("verification")
        if verification:
            v_text = f"{'✓ VERIFIED' if verification.get('verified') else '✗ NOT VERIFIED'}: {verification.get('message', '')[:200]}"
            v_color = "#10b981" if verification.get("verified") else "#ef4444"
            elements.append(Paragraph(f"<font color='{v_color}'><b>{v_text}</b></font>", styles["body"]))
            if verification.get("rows") is not None:
                elements.append(Paragraph(f"Rows: {verification['rows']:,}", styles["body_small"]))

        data_avail = result.get("data_availability")
        if data_avail and data_avail.get("explanation"):
            elements.append(Paragraph(f"<b>Data:</b> {data_avail['explanation'][:250]}", styles["body"]))

        where_lines = result.get("where_condition_lines", [])
        if where_lines:
            elements.append(Paragraph("<b>WHERE Conditions:</b>", styles["label"]))
            for wc in where_lines[:6]:
                ln = wc.get("line_number", "—")
                cond = (wc.get("condition", "") or "")[:150]
                elements.append(Paragraph(f"  Line {ln}: {cond}", styles["mono_small"]))

        having = result.get("having_analysis", [])
        if having:
            elements.append(Paragraph("<b>HAVING Analysis:</b>", styles["label"]))
            for h in having[:4]:
                kills = "KILLS" if h.get("kills_rows") else "OK"
                cond = (h.get("condition", "") or "")[:150]
                elements.append(Paragraph(f"  [{kills}] {cond}", styles["mono_small"]))

    return elements


def _footer_section(styles: dict) -> list:
    elements = []
    elements.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=6))
    elements.append(Paragraph(
        f"This report was auto-generated by {APP_NAME}. For questions, contact your administrator.",
        styles["footer"],
    ))
    return elements


def generate_batch_report(job_ids: list[str]) -> bytes:
    jobs = []
    for jid in job_ids:
        job = get_job(jid)
        if job:
            jobs.append(job)

    if not jobs:
        raise ValueError("No valid jobs found")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"batch_report_{ts}.pdf"
    filepath = PDF_REPORTS_DIR / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(filepath),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title="Batch Diagnostic Report",
        author=APP_NAME,
    )

    styles = _create_styles()
    story = _build_batch_story(jobs, styles)
    doc.build(story)

    pdf_bytes = filepath.read_bytes()
    logger.info("Batch PDF report generated: %s (%d bytes, %d jobs)", filename, len(pdf_bytes), len(jobs))
    return pdf_bytes


def _build_batch_story(jobs: list[dict], styles: dict) -> list:
    story = []
    story.extend(_header_section(styles))

    total = len(jobs)
    with_alerts = sum(1 for j in jobs if j.get("alerts_generated"))
    without_alerts = sum(1 for j in jobs if j.get("status") == "completed" and not j.get("alerts_generated"))
    failed = sum(1 for j in jobs if j.get("status") == "failed")

    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("Batch Run Summary", styles["h2"]))

    summary_data = [
        ["Total Scenarios", str(total)],
        ["With Alerts", str(with_alerts)],
        ["Without Alerts", str(without_alerts)],
        ["Failed", str(failed)],
    ]

    table = Table(summary_data, colWidths=[120, 80], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
        ("TEXTCOLOR", (1, 0), (1, -1), DARK),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(table)

    story.append(Spacer(1, 12 * mm))
    story.append(Paragraph("Detailed Job Reports", styles["h2"]))

    for idx, job in enumerate(jobs, 1):
        if idx > 1:
            story.append(Spacer(1, 8 * mm))
            story.append(HRFlowable(width="100%", thickness=0.5, color=ACCENT, spaceBefore=4, spaceAfter=4))

        metadata = _load_metadata(job)
        story.append(Spacer(1, 4 * mm))
        story.extend(_summary_section(job, metadata, styles))

        story.append(Spacer(1, 4 * mm))
        story.extend(_steps_section(job, styles))

        cte_results = _parse_json(job.get("cte_results_json"))
        if cte_results:
            story.append(Spacer(1, 6 * mm))
            story.extend(_cte_waterfall_section(cte_results, styles))

        diagnostic_results = _parse_json(job.get("result_json"))
        if diagnostic_results:
            story.append(Spacer(1, 6 * mm))
            story.extend(_root_cause_section(job, diagnostic_results, styles))

    story.append(Spacer(1, 10 * mm))
    story.extend(_footer_section(styles))
    return story


def _load_metadata(job: dict) -> dict | None:
    output_dir = job.get("output_dir")
    if not output_dir:
        return None
    metadata_path = Path(output_dir) / "metadata.json"
    if not metadata_path.exists():
        return None
    try:
        with open(metadata_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _parse_json(raw: str | None):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _format_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _format_dt_str(ts: str | None) -> str:
    if not ts:
        return ""
    try:
        if "T" in ts:
            return ts[:19].replace("T", " ")
        return ts[:19]
    except Exception:
        return ts[:19] if len(ts) > 10 else ts