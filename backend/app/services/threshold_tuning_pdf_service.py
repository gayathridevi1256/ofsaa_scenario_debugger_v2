"""PDF report generation for the standalone Threshold Tuning workflow.

Takes the already-fetched analysis + recommendation results exactly as
returned by /threshold-tuning/analyze and /threshold-tuning/recommend — this
module does no further Oracle/SSH work of its own; the SSH batch-date step,
real dataset-query re-executions, and binary-search convergence all already
happened live when those endpoints were called. This is purely a formatting
step over numbers that were already verified.

The one AI touch is a short, deliberately numberless executive-summary
paragraph (see ai_recommendation_service.generate_threshold_tuning_summary)
— every number in the report itself is inserted by Python.
"""

import logging
import re
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable

from app.config import APP_NAME, APP_VERSION, PDF_REPORTS_DIR
from app.services.pdf_service import (
    _create_styles,
    _footer_section,
    _format_dt,
    ACCENT,
    DARK,
    MUTED,
    BORDER,
    BG_STRIPE,
    SUCCESS_BG,
)
from app.services.ai_recommendation_service import generate_threshold_tuning_summary

logger = logging.getLogger(__name__)


def _safe_filename(scenario_name: str, tshld_set_id: str) -> str:
    base = f"{scenario_name}_TSHLD_{tshld_set_id}_threshold_tuning"
    base = re.sub(r"[^A-Za-z0-9_\-]", "_", base).strip("_")
    return f"{base}.pdf"


def _header_section(styles: dict) -> list:
    """A variant of pdf_service._header_section — reused wholesale it says
    'Diagnostic Report', which is misleading directly above this report's
    own 'Threshold Tuning Report' title."""
    elements = []
    elements.append(HRFlowable(width="100%", thickness=2, color=ACCENT, spaceAfter=6))
    elements.append(Paragraph(APP_NAME, styles["h1"]))
    elements.append(Paragraph(f"Threshold Tuning Report  ·  v{APP_VERSION}", styles["body_small"]))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceBefore=6, spaceAfter=6))
    return elements


def _try_float(v):
    try:
        return float(str(v).strip("'\""))
    except (TypeError, ValueError):
        return None


def _fmt_num(v) -> str:
    f = _try_float(v)
    if f is None:
        return "—" if v is None else str(v)
    return f"{f:,.0f}" if f == int(f) else f"{f:,.2f}"


def _fmt_current(v) -> str:
    """Same comma-formatting as _fmt_num for numeric config values, but
    passes non-numeric ones (flags/lists like "'N'" or a long quoted list)
    through as-is rather than showing '—', only truncating if very long."""
    if v is None:
        return "—"
    f = _try_float(v)
    if f is not None:
        return _fmt_num(f)
    s = str(v)
    return s if len(s) <= 80 else s[:77] + "..."


def generate_threshold_tuning_report(analysis: dict, recommendation: dict) -> tuple[bytes, str]:
    """Returns (pdf_bytes, filename). `analysis` and `recommendation` are
    the exact dicts already returned by the analyze/recommend endpoints —
    every figure in the report was already live-verified there; this
    function only formats them."""
    scenario_name = analysis.get("scenario_name") or "UNKNOWN_SCENARIO"
    tshld_set_id = str(analysis.get("tshld_set_id") or "?")
    filename = _safe_filename(scenario_name, tshld_set_id)

    filepath = PDF_REPORTS_DIR / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(filepath),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title=f"Threshold Tuning Report - {scenario_name} (Set {tshld_set_id})",
        author=APP_NAME,
    )

    styles = _create_styles()
    story = _build_story(analysis, recommendation, scenario_name, tshld_set_id, styles)
    doc.build(story)

    pdf_bytes = filepath.read_bytes()
    logger.info("Threshold tuning PDF report generated: %s (%d bytes)", filename, len(pdf_bytes))
    return pdf_bytes, filename


def _build_story(analysis: dict, recommendation: dict, scenario_name: str, tshld_set_id: str, styles: dict) -> list:
    story = []
    story.extend(_header_section(styles))
    story.append(Spacer(1, 6 * mm))
    story.extend(_summary_section(analysis, recommendation, scenario_name, tshld_set_id, styles))
    story.append(Spacer(1, 8 * mm))
    story.extend(_threshold_table_section(analysis, recommendation, styles))
    story.append(Spacer(1, 10 * mm))
    story.extend(_footer_section(styles))
    return story


def _summary_section(analysis: dict, recommendation: dict, scenario_name: str, tshld_set_id: str, styles: dict) -> list:
    elements = []
    elements.append(Paragraph("Threshold Tuning Report", styles["h1"]))
    elements.append(Spacer(1, 2 * mm))
    elements.append(Paragraph(f"<b>{scenario_name}</b> &nbsp;&middot;&nbsp; Threshold Set {tshld_set_id}", styles["h2"]))
    elements.append(Paragraph(f"Generated: {_format_dt(datetime.now(timezone.utc))}", styles["body_small"]))
    elements.append(Spacer(1, 4 * mm))

    current_count = recommendation.get("current_alert_count")
    if current_count is None:
        current_count = analysis.get("alert_count")
    projected_count = recommendation.get("projected_alert_count")
    target_pct = recommendation.get("target_reduction_pct")
    target_met = recommendation.get("target_met")
    applied = recommendation.get("applied_changes") or []

    if projected_count is not None and current_count is not None and current_count > 0:
        if projected_count < current_count:
            direction_note = "The recommended changes would reduce alert volume."
        elif projected_count > current_count:
            direction_note = "The recommended changes would increase alert volume."
        else:
            direction_note = "The recommended changes would leave alert volume unchanged."
    else:
        direction_note = "No numeric threshold changes were found to apply."

    summary_text = None
    try:
        summary_text = generate_threshold_tuning_summary(
            scenario_name, tshld_set_id, target_pct, target_met, len(applied), direction_note,
        )
    except Exception as e:
        logger.warning("Threshold tuning summary generation failed, using fallback text: %s", e)

    if not summary_text:
        summary_text = (
            f"This report summarizes a proposed threshold tuning for the {scenario_name} scenario "
            f"(threshold set {tshld_set_id}), based on real transaction data sampled directly from the "
            f"environment. {direction_note} All figures below were verified by re-executing the "
            f"scenario's actual dataset query against the database. Review and approve with the "
            f"business/scenario owner before applying these changes to production."
        )
    elements.append(Paragraph(summary_text, styles["body"]))
    elements.append(Spacer(1, 4 * mm))

    data = [["Current Alert Count", f"{current_count:,}" if current_count is not None else "—"]]
    if projected_count is not None:
        data.append(["Projected Alert Count", f"{projected_count:,}"])
        delta = recommendation.get("alert_count_delta")
        if delta is not None:
            sign = "+" if delta > 0 else ""
            pct_txt = ""
            if current_count:
                pct_txt = f" ({sign}{(delta / current_count * 100):.1f}%)"
            data.append(["Change", f"{sign}{delta:,}{pct_txt}"])
    if target_pct is not None:
        data.append(["Target Reduction Requested", f"{target_pct:g}%"])
        data.append(["Target Met", "Yes" if target_met else "No — capped by the configured threshold range"])
    data.append(["Thresholds With a Recommended Change", str(len(applied))])

    table = Table(data, colWidths=[200, 280], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
        ("TEXTCOLOR", (1, 0), (1, -1), DARK),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    elements.append(table)

    if applied:
        elements.append(Spacer(1, 4 * mm))
        elements.append(Paragraph("Applied Changes", styles["h3"]))
        lines = "<br/>".join(
            f"&#8226; <b>{c.get('display_name') or c.get('name')}</b>: "
            f"{_fmt_num(c.get('current_value'))} &rarr; {_fmt_num(c.get('recommended_value'))}"
            for c in applied
        )
        elements.append(Paragraph(lines, styles["body"]))

    return elements


def _threshold_table_section(analysis: dict, recommendation: dict, styles: dict) -> list:
    elements = []
    elements.append(Paragraph("Threshold Details", styles["h2"]))

    recs = recommendation.get("recommendations") or {}
    header = [
        Paragraph("<b>Threshold</b>", styles["body"]),
        Paragraph("<b>Current</b>", styles["body"]),
        Paragraph("<b>Recommended</b>", styles["body"]),
        Paragraph("<b>Notes</b>", styles["body_small"]),
    ]
    rows = [header]
    style_cmds = [
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ("TEXTCOLOR", (0, 0), (-1, 0), DARK),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.3, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]

    thresholds = sorted(
        analysis.get("thresholds") or [],
        key=lambda t: t.get("display_name") or t.get("name") or "",
    )
    for t in thresholds:
        name = t.get("name")
        rec = recs.get(name) or {}
        recommended_value = rec.get("recommended_value")
        current_value = t.get("current_value")
        changed = recommended_value is not None and _try_float(recommended_value) != _try_float(current_value)

        row_idx = len(rows)
        rows.append([
            Paragraph(t.get("display_name") or name or "?", styles["body"]),
            Paragraph(_fmt_current(current_value), styles["body"]),
            Paragraph(_fmt_num(recommended_value) if recommended_value is not None else "N/A", styles["body"]),
            Paragraph((rec.get("note") or "")[:240], styles["body_small"]),
        ])
        if changed:
            style_cmds.append(("BACKGROUND", (0, row_idx), (-1, row_idx), SUCCESS_BG))
        elif row_idx % 2 == 0:
            style_cmds.append(("BACKGROUND", (0, row_idx), (-1, row_idx), BG_STRIPE))

    col_widths = [140, 65, 85, 210]
    table = Table(rows, colWidths=col_widths, hAlign="LEFT", repeatRows=1)
    table.setStyle(TableStyle(style_cmds))
    elements.append(table)
    return elements
