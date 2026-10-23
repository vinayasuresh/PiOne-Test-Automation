"""Script to generate the enterprise PowerPoint presentation for PiOne Testmate with embedded UI screenshots."""
import os
from pathlib import Path
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import PP_ALIGN
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE

def create_deck():
    output_dir = Path(__file__).resolve().parents[1] / "presentation"
    img_dir = output_dir / "images"
    output_dir.mkdir(parents=True, exist_ok=True)
    pptx_path = output_dir / "pione_testmate_presentation.pptx"

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    # Color Palette
    C_NAVY = RGBColor(11, 25, 44)       # #0B192C
    C_SLATE = RGBColor(30, 41, 59)      # #1E293B
    C_BLUE = RGBColor(37, 99, 235)      # #2563EB
    C_CYAN = RGBColor(6, 182, 212)      # #06B6D4
    C_EMERALD = RGBColor(16, 185, 129)  # #10B981
    C_AMBER = RGBColor(245, 158, 11)    # #F59E0B
    C_RED = RGBColor(239, 68, 68)       # #EF4444
    C_WHITE = RGBColor(255, 255, 255)
    C_BG_LIGHT = RGBColor(248, 250, 252) # #F8FAFC
    C_MUTED = RGBColor(100, 116, 139)   # #64748B
    C_BORDER = RGBColor(226, 232, 240)  # #E2E8F0

    blank_layout = prs.slide_layouts[6]

    def add_header(slide, title_text, category_text="PIONE TESTMATE ENTERPRISE"):
        cat_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.45), Inches(11.7), Inches(0.35))
        tf_cat = cat_box.text_frame
        tf_cat.word_wrap = True
        p_cat = tf_cat.paragraphs[0]
        p_cat.text = category_text.upper()
        p_cat.font.size = Pt(10)
        p_cat.font.bold = True
        p_cat.font.color.rgb = C_BLUE

        title_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.75), Inches(11.7), Inches(0.65))
        tf = title_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = title_text
        p.font.size = Pt(22)
        p.font.bold = True
        p.font.color.rgb = C_NAVY

    def add_card(slide, left, top, width, height, bg_color=C_WHITE, border_color=C_BORDER):
        shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
        shape.fill.solid()
        shape.fill.fore_color.rgb = bg_color
        shape.line.color.rgb = border_color
        shape.line.width = Pt(1.5)
        return shape

    def add_split_slide(slide, title, category, headline, points, guarantee_text, img_name, caption):
        add_header(slide, title, category)

        # Left Column: Value Proposition & Feature Card
        card_left = Inches(0.8)
        card_top = Inches(1.6)
        card_w = Inches(5.1)
        card_h = Inches(5.3)

        add_card(slide, card_left, card_top, card_w, card_h)
        tb = slide.shapes.add_textbox(card_left + Inches(0.2), card_top + Inches(0.2), card_w - Inches(0.4), card_h - Inches(0.4))
        tf = tb.text_frame
        tf.word_wrap = True

        # Headline
        p_head = tf.paragraphs[0]
        p_head.text = headline
        p_head.font.size = Pt(16)
        p_head.font.bold = True
        p_head.font.color.rgb = C_NAVY

        # Bullets
        for pt in points:
            p_b = tf.add_paragraph()
            p_b.text = "✔ " + pt
            p_b.font.size = Pt(11.5)
            p_b.font.color.rgb = C_SLATE
            p_b.space_before = Pt(8)

        # Enterprise Guarantee Badge
        if guarantee_text:
            p_g = tf.add_paragraph()
            p_g.text = f"🛡 Enterprise Guarantee: {guarantee_text}"
            p_g.font.size = Pt(11)
            p_g.font.bold = True
            p_g.font.color.rgb = C_BLUE
            p_g.space_before = Pt(14)

        # Right Column: Screenshot Visual Frame
        img_path = img_dir / img_name
        if img_path.exists():
            img_left = Inches(6.1)
            img_top = Inches(1.6)
            img_w = Inches(6.4)
            img_h = Inches(4.7)

            # Framing Card behind screenshot
            add_card(slide, img_left - Inches(0.08), img_top - Inches(0.08), img_w + Inches(0.16), img_h + Inches(0.6), bg_color=C_WHITE, border_color=C_BLUE)

            # Insert Image
            slide.shapes.add_picture(str(img_path), img_left, img_top, width=img_w, height=img_h)

            # Caption below image
            cap_box = slide.shapes.add_textbox(img_left, img_top + img_h + Inches(0.05), img_w, Inches(0.4))
            tf_cap = cap_box.text_frame
            p_cap = tf_cap.paragraphs[0]
            p_cap.text = "LIVE PRODUCT CAPTURE: " + caption.upper()
            p_cap.font.size = Pt(9.5)
            p_cap.font.bold = True
            p_cap.font.color.rgb = C_MUTED
            p_cap.alignment = PP_ALIGN.CENTER

    # ==========================================
    # SLIDE 1: Title Slide (Dark Theme)
    # ==========================================
    slide1 = prs.slides.add_slide(blank_layout)
    bg1 = slide1.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(13.333), Inches(7.5))
    bg1.fill.solid()
    bg1.fill.fore_color.rgb = C_NAVY
    bg1.line.fill.background()

    pill = slide1.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(1.2), Inches(1.5), Inches(4.5), Inches(0.45))
    pill.fill.solid()
    pill.fill.fore_color.rgb = C_BLUE
    pill.line.fill.background()
    p_pill = pill.text_frame.paragraphs[0]
    p_pill.text = "ENTERPRISE UI AUTOMATION PLATFORM v2.1"
    p_pill.font.size = Pt(11)
    p_pill.font.bold = True
    p_pill.font.color.rgb = C_WHITE
    p_pill.alignment = PP_ALIGN.CENTER

    t_box1 = slide1.shapes.add_textbox(Inches(1.2), Inches(2.2), Inches(11), Inches(1.8))
    tf1 = t_box1.text_frame
    p1 = tf1.paragraphs[0]
    p1.text = "PiOne Testmate"
    p1.font.size = Pt(46)
    p1.font.bold = True
    p1.font.color.rgb = C_WHITE

    p1_sub = tf1.add_paragraph()
    p1_sub.text = "Deterministic UI Scanner, Grounded AI Test Planner & Resilient Regression Engine"
    p1_sub.font.size = Pt(20)
    p1_sub.font.color.rgb = C_CYAN

    d_box1 = slide1.shapes.add_textbox(Inches(1.2), Inches(4.3), Inches(10), Inches(1.2))
    tf_d = d_box1.text_frame
    p_d = tf_d.paragraphs[0]
    p_d.text = "• 100% Playwright Synchronous Core Engine (No Flaky Selectors)\n• Neutral Multi-Provider AI Gateway (Ollama, Gemini, Claude, OpenAI)\n• Passive DAST Assessment Mapped to OWASP Top 10 (2021)\n• 10-Sheet Audit-Ready Workbooks & In-Place Session Lifecycle Protection"
    p_d.font.size = Pt(14)
    p_d.font.color.rgb = RGBColor(203, 213, 225)

    f_box1 = slide1.shapes.add_textbox(Inches(1.2), Inches(6.2), Inches(10), Inches(0.5))
    f_p = f_box1.text_frame.paragraphs[0]
    f_p.text = "PiOne Technologies LLP • Production Platform Presentation • Confidential"
    f_p.font.size = Pt(11)
    f_p.font.color.rgb = C_MUTED

    # ==========================================
    # SLIDE 2: The Core Problem
    # ==========================================
    slide2 = prs.slides.add_slide(blank_layout)
    add_header(slide2, "The $3.6M Problem: The 3 Broken Pillars of UI Automation", "Executive Problem Statement")

    col_w = Inches(3.6)
    gap = Inches(0.4)
    top_pos = Inches(1.6)
    h_pos = Inches(5.1)

    # Card 1
    add_card(slide2, Inches(0.8), top_pos, col_w, h_pos)
    tb = slide2.shapes.add_textbox(Inches(1.0), top_pos + Inches(0.2), col_w - Inches(0.4), h_pos - Inches(0.4))
    tf = tb.text_frame; tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = "1. Brittle Locators & Flakiness"
    p.font.size = Pt(17); p.font.bold = True; p.font.color.rgb = C_RED
    bullets1 = [
        "Traditional suites bind to dynamic CSS or relative XPaths.",
        "Minor styling, wrapper, or Tailwind updates break 40% of test suites.",
        "Teams waste hundreds of hours fixing locators instead of testing product features."
    ]
    for b in bullets1:
        p_b = tf.add_paragraph()
        p_b.text = "• " + b
        p_b.font.size = Pt(12); p_b.font.color.rgb = C_SLATE; p_b.space_before = Pt(8)

    # Card 2
    add_card(slide2, Inches(0.8) + col_w + gap, top_pos, col_w, h_pos)
    tb = slide2.shapes.add_textbox(Inches(0.8) + col_w + gap + Inches(0.2), top_pos + Inches(0.2), col_w - Inches(0.4), h_pos - Inches(0.4))
    tf = tb.text_frame; tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = "2. Ungrounded AI Hallucinations"
    p.font.size = Pt(17); p.font.bold = True; p.font.color.rgb = C_AMBER
    bullets2 = [
        "Raw AI agents attempting to drive web browsers click destructive actions.",
        "Accidental checkouts, data deletions, and credential leaks cause business risk.",
        "AI models hallucinate non-existent element locators, generating false coverage."
    ]
    for b in bullets2:
        p_b = tf.add_paragraph()
        p_b.text = "• " + b
        p_b.font.size = Pt(12); p_b.font.color.rgb = C_SLATE; p_b.space_before = Pt(8)

    # Card 3
    add_card(slide2, Inches(0.8) + (col_w + gap) * 2, top_pos, col_w, h_pos)
    tb = slide2.shapes.add_textbox(Inches(0.8) + (col_w + gap) * 2 + Inches(0.2), top_pos + Inches(0.2), col_w - Inches(0.4), h_pos - Inches(0.4))
    tf = tb.text_frame; tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = "3. Session & State Fragility"
    p.font.size = Pt(17); p.font.bold = True; p.font.color.rgb = C_BLUE
    bullets3 = [
        "In modern SPAs, authentication tokens expire silently during long crawler passes.",
        "A 2-hour end-to-end regression fails completely when a cookie expires.",
        "Legacy tools discard all progress and force engineers to restart from scratch."
    ]
    for b in bullets3:
        p_b = tf.add_paragraph()
        p_b.text = "• " + b
        p_b.font.size = Pt(12); p_b.font.color.rgb = C_SLATE; p_b.space_before = Pt(8)

    # ==========================================
    # SLIDE 3: Architecture & Guiding Philosophy
    # ==========================================
    slide3 = prs.slides.add_slide(blank_layout)
    add_header(slide3, "Core Philosophy: Deterministic Scanner First, AI Second", "Architecture Paradigm")

    quote_card = add_card(slide3, Inches(0.8), Inches(1.6), Inches(11.7), Inches(1.1), bg_color=RGBColor(238, 242, 255), border_color=C_BLUE)
    tb_q = slide3.shapes.add_textbox(Inches(1.0), Inches(1.7), Inches(11.3), Inches(0.9))
    p_q = tb_q.text_frame.paragraphs[0]
    p_q.text = "“Playwright owns browser discovery, DOM analysis, and execution deterministically.\nAI models synthesize test logic based strictly on saved evidence—AI never drives the browser directly.”"
    p_q.font.size = Pt(14.5); p_q.font.bold = True; p_q.font.color.rgb = C_BLUE

    # Left Box
    add_card(slide3, Inches(0.8), Inches(2.9), Inches(5.6), Inches(4.0))
    tb_det = slide3.shapes.add_textbox(Inches(1.0), Inches(3.1), Inches(5.2), Inches(3.6))
    tf_det = tb_det.text_frame; tf_det.word_wrap = True
    p_d_head = tf_det.paragraphs[0]
    p_d_head.text = "Deterministic Crawler Engine"
    p_d_head.font.size = Pt(17); p_d_head.font.bold = True; p_d_head.font.color.rgb = C_NAVY
    items_det = [
        "SSRF Guardrails: Rejects RFC 1918, loopbacks, and cloud metadata.",
        "Deep Scrolling: Explores document viewports & nested containers.",
        "Overlay Engine: Probes menus/dropdowns and cleans up via Escape key.",
        "12-Attribute Fingerprinting: Role > ARIA > Text > CSS > XPath.",
        "Destructive Action Parking Lot: Segregates unsafe mutations."
    ]
    for it in items_det:
        p_it = tf_det.add_paragraph()
        p_it.text = "✔ " + it
        p_it.font.size = Pt(12); p_it.font.color.rgb = C_SLATE; p_it.space_before = Pt(6)

    # Right Box
    add_card(slide3, Inches(6.9), Inches(2.9), Inches(5.6), Inches(4.0))
    tb_ai = slide3.shapes.add_textbox(Inches(7.1), Inches(3.1), Inches(5.2), Inches(3.6))
    tf_ai = tb_ai.text_frame; tf_ai.word_wrap = True
    p_ai_head = tf_ai.paragraphs[0]
    p_ai_head.text = "Neutral Multi-Provider AI Gateway"
    p_ai_head.font.size = Pt(17); p_ai_head.font.bold = True; p_ai_head.font.color.rgb = C_NAVY
    items_ai = [
        "100% Grounded Test Synthesis: References verified DOM IDs only.",
        "Multi-Provider Freedom: Ollama, Gemini, Claude, OpenAI, Offline Rules.",
        "Requirement Awareness: Ingests user stories, PRDs, and custom prompts.",
        "Zero Hallucination Tolerance: Rejects unverified target elements.",
        "Controlled Execution: Verifies assertions and captures step screenshots."
    ]
    for it in items_ai:
        p_it = tf_ai.add_paragraph()
        p_it.text = "✔ " + it
        p_it.font.size = Pt(12); p_it.font.color.rgb = C_SLATE; p_it.space_before = Pt(6)

    # ==========================================
    # SLIDE 4: Step 1 — Scan Application
    # ==========================================
    slide4 = prs.slides.add_slide(blank_layout)
    add_split_slide(
        slide4,
        title="Step 1: Zero-Trust Target Setup & SSRF Protection",
        category="Step 1: Scan Application",
        headline="Target Origin Locking with Kernel-Level SSRF Guardrails",
        points=[
            "Strict Origin Validation: Locks the crawler to the exact application origin, preventing cross-domain creeping.",
            "SSRF IP-Level Guard: Automatically blocks RFC 1918 private IPs, loopbacks, and cloud metadata (169.254.169.254).",
            "Zero Credential Storage: Passwords and tokens are never stored in localStorage, URLs, or checkpoints.",
            "Segmented Mode Switcher: Toggle between Live Target Scan and 100% Offline Mock/Demo Simulation."
        ],
        guarantee_text="Zero internal network leakage and absolute target isolation.",
        img_name="01_scan_application.png",
        caption="Step 1 Dashboard: Target URL, Password Reveal & Mode Configuration"
    )

    # ==========================================
    # SLIDE 5: Step 2 — Choose Modules
    # ==========================================
    slide5 = prs.slides.add_slide(blank_layout)
    add_split_slide(
        slide5,
        title="Step 2: Autonomous Module Discovery & Boundary Selection",
        category="Step 2: Choose Modules",
        headline="Automatic Navigation Mapping without Crawling Noise",
        points=[
            "Passive Navigation Detection: Automatically extracts top navigation bars, sidebars, and functional modules.",
            "Granular Scope Selection: Select single, multiple, or all modules (e.g. Inventory and Cart, excluding Admin).",
            "Dynamic Selection Chips: Real-time visual feedback on selected application boundaries with instant removal.",
            "Zero Traversal Overrun: Unselected modules and external links are strictly excluded from subsequent crawling."
        ],
        guarantee_text="Scans only what you select; zero wasted crawling cycles.",
        img_name="02_choose_modules.png",
        caption="Step 2 Dashboard: Discovered Application Modules & Scope Checkboxes"
    )

    # ==========================================
    # SLIDE 6: Step 3 — Manual Review
    # ==========================================
    slide6 = prs.slides.add_slide(blank_layout)
    add_split_slide(
        slide6,
        title="Step 3: Human-in-the-Loop Safety Governance",
        category="Step 3: Manual Review",
        headline="Pre-Crawl Safety Checkpoint & Traversal Verification",
        points=[
            "Governance Gate: Provides explicit human review of confirmed module scopes prior to launching Playwright.",
            "Destructive Action Policy Preview: Outlines how mutations, deletes, and checkouts will be safely diverted.",
            "Sequential Traversal Assurance: Enforces module-by-module exploration with scroll and state restoration.",
            "Durable Checkpoint Initialization: Initializes atomic on-disk recovery checkpoints before scanning starts."
        ],
        guarantee_text="No automated crawler launches without explicit operator authorization.",
        img_name="03_manual_review.png",
        caption="Step 3 Dashboard: Pre-Crawl Scope Confirmation & Safety Guardrails"
    )

    # ==========================================
    # SLIDE 7: Step 4 — Deep Crawling & Telemetry
    # ==========================================
    slide7 = prs.slides.add_slide(blank_layout)
    add_split_slide(
        slide7,
        title="Step 4: Deep Crawling & Live Telemetry Stream",
        category="Step 4: Crawl Progress",
        headline="Real-Time Exploration with Deep Scrolling & Full Telemetry",
        points=[
            "Viewport Deep Scanning: Explores document viewports and nested scrollable containers for lazy-loaded items.",
            "Menu Overlay Probing: Discovers flyouts and dropdowns, restoring page state cleanly via the Escape key.",
            "Live Telemetry Stream: Real-time gauges for module completion, route progression, and discovered elements.",
            "Interactive Scan Controls: Real-time Pause, Resume, End & Save, and Discard controls."
        ],
        guarantee_text="Destructive buttons parked with 100% zero-click safety.",
        img_name="04_crawl_telemetry.png",
        caption="Step 4 Dashboard: Live Module/Route Progress Gauges & Interactive Controls"
    )

    # ==========================================
    # SLIDE 8: Step 5 — UI Intelligence & Route Health
    # ==========================================
    slide8 = prs.slides.add_slide(blank_layout)
    add_split_slide(
        slide8,
        title="Step 5: UI Intelligence Catalog & Route Health Scoring",
        category="Step 5: Analyze",
        headline="12-Attribute Element Fingerprinting & Route Quality Scoring",
        points=[
            "Route Health Scoring: Classifies routes as WORKING (>=70), PARTIAL (40-69), or NON-WORKING (<40).",
            "12-Attribute Fingerprinting: Registers Role, ARIA, Text, ID, Name, TestIDs, CSS, XPath, and Coordinates.",
            "Hierarchical Selector Confidence: Resolves elements using Role > ARIA > ID > Text > CSS > XPath.",
            "Destructive Parking Lot Review: Inspects all blocked actions with screenshot evidence and selector signatures."
        ],
        guarantee_text="Zero flaky locators; tests survive UI redesigns and CSS refactors.",
        img_name="05_route_health_catalog.png",
        caption="Step 5 Dashboard: Route Health Scoring Cards & Scanned Routes Table"
    )

    # ==========================================
    # SLIDE 9: Step 6 — Safe Passive DAST Assessment
    # ==========================================
    slide9 = prs.slides.add_slide(blank_layout)
    add_split_slide(
        slide9,
        title="Step 6: Safe Passive DAST & OWASP Top 10 (2021) Mapping",
        category="Step 6: Security Review",
        headline="Authorized, Passive Security Hygiene Assessment",
        points=[
            "OWASP Top 10 (2021) Mapping: Categorizes findings into A01 Broken Access, A02 Crypto, A05 Misconfig, etc.",
            "Security Headers & Cookies: Checks CSP, HSTS, nosniff, Referrer-Policy, HttpOnly, Secure, and SameSite.",
            "CORS & Exposure Signals: Flags wildcard CORS with credentials, exposed JWTs, API keys, and DB URIs.",
            "Zero Destructive Actions: Non-intrusive GET requests paced at 1 req/sec; zero brute force or fuzzing."
        ],
        guarantee_text="Multi-format exports in JSON, Markdown, and Excel (.xlsx).",
        img_name="06_security_assessment.png",
        caption="Step 6 Security Panel: Severity Cards, Filterable Findings & Export Controls"
    )

    # ==========================================
    # SLIDE 10: Step 7 — Multi-Provider AI Planning
    # ==========================================
    slide10 = prs.slides.add_slide(blank_layout)
    add_split_slide(
        slide10,
        title="Step 7: Grounded Multi-Provider AI Test Planning",
        category="Step 7: Generate Tests",
        headline="Neutral AI Gateway with Verified Element Grounding",
        points=[
            "Multi-Provider Freedom: Choose Offline Deterministic, Ollama (Local), Google Gemini, Claude, or OpenAI.",
            "100% Grounded Test Steps: Every generated step binds strictly to a verified element ID from Step 5.",
            "Requirement Document Ingestion: Upload PRDs, user stories (.txt, .md, .csv) to guide test scenario generation.",
            "Zero Selector Hallucination: Unverified locators are rejected by the normalizer before execution."
        ],
        guarantee_text="Synthesizes complete, executable Pytest scripts automatically.",
        img_name="07_ai_test_planning.png",
        caption="Step 7 Dashboard: AI Provider Selection, Context Ingestion & Generated Plan"
    )

    # ==========================================
    # SLIDE 11: Step 8 — Controlled Execution & Evidence
    # ==========================================
    slide11 = prs.slides.add_slide(blank_layout)
    add_split_slide(
        slide11,
        title="Step 8: Controlled Execution & Per-Step Screenshot Evidence",
        category="Step 8: Execute Tests",
        headline="Audit-Grade Test Execution with Full Diagnostic Telemetry",
        points=[
            "Playwright Test Driver: Executes real browser navigation and assertions with restored authentication state.",
            "Per-Step Screenshots: Automatically captures full-resolution PNG images for every single action.",
            "Inline Lightbox Evidence: Click any test step thumbnail to inspect evidence directly in the dashboard.",
            "Single-Step Rerun: Fast troubleshooting of individual steps without re-executing entire suites."
        ],
        guarantee_text="Exact locator telemetry and console/network failure capture.",
        img_name="08_controlled_execution.png",
        caption="Step 8 Dashboard: Test Step Execution Outcomes & Evidence Thumbnails"
    )

    # ==========================================
    # SLIDE 12: Step 9 — 10-Sheet Excel Workbook
    # ==========================================
    slide12 = prs.slides.add_slide(blank_layout)
    add_split_slide(
        slide12,
        title="Step 9: Standard 10-Sheet Enterprise Audit-Ready Workbook",
        category="Step 9: Audit Reports",
        headline="Complete Transparency & Multi-Format Audit Artifacts",
        points=[
            "10-Sheet Excel Architecture: working_routes, partial_routes, non_working_routes, menu_hierarchy, element_map, parking_lot_actions, scan_events, test_cases, execution_results, coverage_gaps.",
            "Formula Injection Sanitization: Cleans cell values to prevent spreadsheet injection vulnerabilities.",
            "Multi-Format Packages: Download JSON catalog, YAML schema, Markdown summary, and Screenshots ZIP.",
            "Reusable Baselines: Save current scan state as a named baseline for regression diff comparisons."
        ],
        guarantee_text="Satisfies both technical QA and enterprise regulatory compliance.",
        img_name="09_audit_reports.png",
        caption="Step 9 Dashboard: Grouped Report Downloads & Baseline Management"
    )

    # ==========================================
    # SLIDE 13: Saved Runs & Checkpoint Recovery
    # ==========================================
    slide13 = prs.slides.add_slide(blank_layout)
    add_split_slide(
        slide13,
        title="Enterprise Resilience: Saved Runs & Checkpoint Recovery",
        category="Platform Management: Saved Runs",
        headline="Durable SQLite / PostgreSQL Persistence with Zero Data Loss",
        points=[
            "Historical Runs Catalog: Displays target URL, run status, progress %, module counts, and timestamps.",
            "Instant Checkpoint Resumption: Reconnect to interrupted or paused crawls and resume without data loss.",
            "Run Duplication & Archiving: Clone previous run parameters into a fresh run or archive completed runs.",
            "In-Place Session Renewal: /session/refresh re-authenticates expired sessions without losing run context."
        ],
        guarantee_text="Eliminates the multi-hour test run crash problem completely.",
        img_name="10_saved_runs_dashboard.png",
        caption="Saved Runs Management View: Historical Scans, Metrics & Actions"
    )

    # ==========================================
    # SLIDE 14: Named Regression Suites & Baselines
    # ==========================================
    slide14 = prs.slides.add_slide(blank_layout)
    add_split_slide(
        slide14,
        title="Continuous Regression: Named Suites & Replay Diffing",
        category="Platform Management: Regression Suites",
        headline="Selectable Test Execution & Automated Regression Diffing",
        points=[
            "Named Regression Suites: Group tests by functional module, release tag, or test environment.",
            "Selectable Execution: Select specific test cases with an explicit confirmation modal before launching Playwright.",
            "Typed DELETE Safeguards: Bulk deletion requires typing 'DELETE', eliminating accidental data destruction.",
            "Baseline Regression Diffing: Calculates status transitions (PASSED -> FAILED), new routes, and selector drift."
        ],
        guarantee_text="Instant visual diff reports for pull requests and CI/CD gates.",
        img_name="11_regression_suites.png",
        caption="Regression Suites View: Named Suites, Filter Pills & Selectable Execution"
    )

    # ==========================================
    # SLIDE 15: Production Benchmarks & Summary
    # ==========================================
    slide15 = prs.slides.add_slide(blank_layout)
    add_header(slide15, "Production Verification Benchmarks & Executive Summary", "Enterprise Summary")

    m_w = Inches(2.7)
    m_h = Inches(1.8)
    metrics = [
        ("167 / 167", "Pytest Suite Passing (100%)", C_EMERALD),
        ("0 Flaky Locators", "12-Attribute Fingerprints", C_BLUE),
        ("100% Safe", "Zero Destructive Actions", C_AMBER),
        ("10 Sheets", "Audit-Ready Excel Workbooks", C_NAVY)
    ]
    for idx, (m_val, m_lbl, m_col) in enumerate(metrics):
        x = Inches(0.8) + idx * (m_w + Inches(0.3))
        add_card(slide15, x, Inches(1.6), m_w, m_h)
        tb_m = slide15.shapes.add_textbox(x, Inches(1.9), m_w, Inches(1.2))
        tf_m = tb_m.text_frame
        p_val = tf_m.paragraphs[0]
        p_val.text = m_val
        p_val.font.size = Pt(28); p_val.font.bold = True; p_val.font.color.rgb = m_col; p_val.alignment = PP_ALIGN.CENTER
        p_lbl = tf_m.add_paragraph()
        p_lbl.text = m_lbl
        p_lbl.font.size = Pt(12); p_lbl.font.color.rgb = C_SLATE; p_lbl.alignment = PP_ALIGN.CENTER

    add_card(slide15, Inches(0.8), Inches(3.8), Inches(11.7), Inches(3.1))
    tb_c = slide15.shapes.add_textbox(Inches(1.0), Inches(4.0), Inches(11.3), Inches(2.7))
    tf_c = tb_c.text_frame; tf_c.word_wrap = True
    p_c_head = tf_c.paragraphs[0]
    p_c_head.text = "Executive Value Summary: Why Enterprises Choose PiOne Testmate"
    p_c_head.font.size = Pt(17); p_c_head.font.bold = True; p_c_head.font.color.rgb = C_NAVY
    conclusions = [
        "75% Reduction in Test Maintenance: 12-attribute resilient fingerprinting eliminates flaky selector breakdowns.",
        "Zero-Risk Safety Architecture: Parking lot action segregation and SSRF IP-level guards protect production data.",
        "Session & State Resiliency: In-place session refresh (/session/refresh) and atomic checkpoints guarantee test completion.",
        "Neutral AI Economics: Deploy with local open-weights models (Ollama), cloud APIs, or offline rules—zero vendor lock-in.",
        "Turnkey Compliance: Standard 10-sheet workbooks, step screenshots, and OWASP Top 10 security reviews satisfy technical and regulatory audits."
    ]
    for c in conclusions:
        p_c = tf_c.add_paragraph()
        p_c.text = "• " + c
        p_c.font.size = Pt(12); p_c.font.color.rgb = C_SLATE; p_c.space_before = Pt(4)

    prs.save(str(pptx_path))
    print(f"\n[SUCCESS] Enhanced PowerPoint presentation generated with screenshots at:\n{pptx_path}\n")

if __name__ == "__main__":
    create_deck()
