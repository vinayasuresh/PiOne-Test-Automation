"""Script to generate the enterprise PowerPoint presentation for PiOne Testmate."""
import os
from pathlib import Path
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import PP_ALIGN
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE

def create_deck():
    output_dir = Path(__file__).resolve().parents[1] / "presentation"
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
    C_BORDER = RGBColor(226, 232, 240)

    blank_layout = prs.slide_layouts[6]

    def add_header(slide, title_text, category_text="PIONE TESTMATE ENTERPRISE"):
        # Top banner category
        cat_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.5), Inches(11.7), Inches(0.4))
        tf_cat = cat_box.text_frame
        tf_cat.word_wrap = True
        p_cat = tf_cat.paragraphs[0]
        p_cat.text = category_text.upper()
        p_cat.font.size = Pt(11)
        p_cat.font.bold = True
        p_cat.font.color.rgb = C_BLUE

        # Main Slide Title
        title_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.85), Inches(11.7), Inches(0.7))
        tf = title_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = title_text
        p.font.size = Pt(24)
        p.font.bold = True
        p.font.color.rgb = C_NAVY

    def add_card(slide, left, top, width, height, bg_color=C_WHITE, border_color=C_BORDER):
        shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
        shape.fill.solid()
        shape.fill.fore_color.rgb = bg_color
        shape.line.color.rgb = border_color
        shape.line.width = Pt(1.5)
        return shape

    # ==========================================
    # SLIDE 1: Title Slide (Dark Theme)
    # ==========================================
    slide1 = prs.slides.add_slide(blank_layout)
    bg1 = slide1.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(13.333), Inches(7.5))
    bg1.fill.solid()
    bg1.fill.fore_color.rgb = C_NAVY
    bg1.line.fill.background()

    # Accent decorative pill
    pill = slide1.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(1.2), Inches(1.8), Inches(4.2), Inches(0.45))
    pill.fill.solid()
    pill.fill.fore_color.rgb = C_BLUE
    pill.line.fill.background()
    p_pill = pill.text_frame.paragraphs[0]
    p_pill.text = "ENTERPRISE UI AUTOMATION PLATFORM v2.1"
    p_pill.font.size = Pt(11)
    p_pill.font.bold = True
    p_pill.font.color.rgb = C_WHITE
    p_pill.alignment = PP_ALIGN.CENTER

    # Main Title
    t_box1 = slide1.shapes.add_textbox(Inches(1.2), Inches(2.5), Inches(11), Inches(1.8))
    tf1 = t_box1.text_frame
    p1 = tf1.paragraphs[0]
    p1.text = "PiOne Testmate"
    p1.font.size = Pt(48)
    p1.font.bold = True
    p1.font.color.rgb = C_WHITE

    p1_sub = tf1.add_paragraph()
    p1_sub.text = "Deterministic UI Crawler, Grounded AI Test Planner & Resilient Regression Engine"
    p1_sub.font.size = Pt(20)
    p1_sub.font.color.rgb = C_CYAN

    # Description
    d_box1 = slide1.shapes.add_textbox(Inches(1.2), Inches(4.5), Inches(10), Inches(1.2))
    tf_d = d_box1.text_frame
    p_d = tf_d.paragraphs[0]
    p_d.text = "Built on Playwright Sync Core • Neutral Multi-Provider AI Gateway • OWASP Top 10 DAST\n10-Sheet Audit-Ready Excel Workbooks • In-Place Session Lifecycle Protection"
    p_d.font.size = Pt(14)
    p_d.font.color.rgb = RGBColor(203, 213, 225)

    # Footer
    f_box1 = slide1.shapes.add_textbox(Inches(1.2), Inches(6.2), Inches(10), Inches(0.5))
    f_p = f_box1.text_frame.paragraphs[0]
    f_p.text = "PiOne Technologies LLP • Confidential & Proprietary Technical Presentation"
    f_p.font.size = Pt(11)
    f_p.font.color.rgb = C_MUTED

    # ==========================================
    # SLIDE 2: The Core Problem
    # ==========================================
    slide2 = prs.slides.add_slide(blank_layout)
    add_header(slide2, "The Critical Dilemma in Enterprise UI Testing", "Problem Statement")

    col_w = Inches(3.6)
    gap = Inches(0.4)
    top_pos = Inches(1.8)
    h_pos = Inches(4.8)

    # Card 1: Flaky Locators
    add_card(slide2, Inches(0.8), top_pos, col_w, h_pos)
    tb = slide2.shapes.add_textbox(Inches(1.0), top_pos + Inches(0.2), col_w - Inches(0.4), h_pos - Inches(0.4))
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = "1. Brittle Locators & Flakiness"
    p.font.size = Pt(18); p.font.bold = True; p.font.color.rgb = C_RED
    bullets1 = [
        "Traditional test suites rely on fragile CSS classes, relative XPaths, or dynamic attributes.",
        "Minor developer styling or wrapper updates break 40% of automation suites.",
        "Engineers waste dozens of hours updating script locators rather than testing product logic."
    ]
    for b in bullets1:
        p_b = tf.add_paragraph()
        p_b.text = "• " + b
        p_b.font.size = Pt(13); p_b.font.color.rgb = C_SLATE; p_b.space_before = Pt(10)

    # Card 2: Ungrounded AI Hallucinations
    add_card(slide2, Inches(0.8) + col_w + gap, top_pos, col_w, h_pos)
    tb = slide2.shapes.add_textbox(Inches(0.8) + col_w + gap + Inches(0.2), top_pos + Inches(0.2), col_w - Inches(0.4), h_pos - Inches(0.4))
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = "2. Ungrounded AI Hallucination"
    p.font.size = Pt(18); p.font.bold = True; p.font.color.rgb = C_AMBER
    bullets2 = [
        "Unconstrained generative agents attempting to 'click around' execute destructive actions.",
        "Accidental checkout, account deletion, or external redirects cause real business loss.",
        "AI models hallucinate non-existent button selectors, resulting in false test coverage."
    ]
    for b in bullets2:
        p_b = tf.add_paragraph()
        p_b.text = "• " + b
        p_b.font.size = Pt(13); p_b.font.color.rgb = C_SLATE; p_b.space_before = Pt(10)

    # Card 3: Session Expiry Failures
    add_card(slide2, Inches(0.8) + (col_w + gap) * 2, top_pos, col_w, h_pos)
    tb = slide2.shapes.add_textbox(Inches(0.8) + (col_w + gap) * 2 + Inches(0.2), top_pos + Inches(0.2), col_w - Inches(0.4), h_pos - Inches(0.4))
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = "3. Session & State Fragility"
    p.font.size = Pt(18); p.font.bold = True; p.font.color.rgb = C_BLUE
    bullets3 = [
        "In modern token-based Single Page Apps, tokens expire silently during long test runs.",
        "A 2-hour crawl or regression suite fails completely when a cookie expires.",
        "No recovery mechanism existed to renew sessions in-place without restarting from scratch."
    ]
    for b in bullets3:
        p_b = tf.add_paragraph()
        p_b.text = "• " + b
        p_b.font.size = Pt(13); p_b.font.color.rgb = C_SLATE; p_b.space_before = Pt(10)

    # ==========================================
    # SLIDE 3: Architecture & Guiding Philosophy
    # ==========================================
    slide3 = prs.slides.add_slide(blank_layout)
    add_header(slide3, "Core Philosophy: Deterministic Scanner First, AI Second", "Architectural Paradigm")

    # Banner quote
    quote_card = add_card(slide3, Inches(0.8), Inches(1.8), Inches(11.7), Inches(1.1), bg_color=RGBColor(238, 242, 255), border_color=C_BLUE)
    tb_q = slide3.shapes.add_textbox(Inches(1.0), Inches(1.9), Inches(11.3), Inches(0.9))
    p_q = tb_q.text_frame.paragraphs[0]
    p_q.text = "“Playwright owns browser discovery, DOM analysis, and execution deterministically.\nAI models synthesize test logic based strictly on saved evidence—AI never drives the browser directly.”"
    p_q.font.size = Pt(15); p_q.font.bold = True; p_q.font.color.rgb = C_BLUE

    # Left Box: Deterministic Engine
    add_card(slide3, Inches(0.8), Inches(3.2), Inches(5.6), Inches(3.6))
    tb_det = slide3.shapes.add_textbox(Inches(1.0), Inches(3.4), Inches(5.2), Inches(3.2))
    tf_det = tb_det.text_frame
    tf_det.word_wrap = True
    p_d_head = tf_det.paragraphs[0]
    p_d_head.text = "Deterministic Crawler Engine"
    p_d_head.font.size = Pt(18); p_d_head.font.bold = True; p_d_head.font.color.rgb = C_NAVY
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
        p_it.font.size = Pt(13); p_it.font.color.rgb = C_SLATE; p_it.space_before = Pt(6)

    # Right Box: Grounded AI Gateway
    add_card(slide3, Inches(6.9), Inches(3.2), Inches(5.6), Inches(3.6))
    tb_ai = slide3.shapes.add_textbox(Inches(7.1), Inches(3.4), Inches(5.2), Inches(3.2))
    tf_ai = tb_ai.text_frame
    tf_ai.word_wrap = True
    p_ai_head = tf_ai.paragraphs[0]
    p_ai_head.text = "Neutral Multi-Provider AI Gateway"
    p_ai_head.font.size = Pt(18); p_ai_head.font.bold = True; p_ai_head.font.color.rgb = C_NAVY
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
        p_it.font.size = Pt(13); p_it.font.color.rgb = C_SLATE; p_it.space_before = Pt(6)

    # ==========================================
    # SLIDE 4: The 8-Step End-to-End Workflow
    # ==========================================
    slide4 = prs.slides.add_slide(blank_layout)
    add_header(slide4, "The 8-Step Enterprise Test Automation Journey", "Workflow Progression")

    steps = [
        ("Step 1", "Scan App", "SSRF validation, credentials & target origin lock."),
        ("Step 2", "Choose Modules", "Passive menu discovery & boundary selection."),
        ("Step 3", "Manual Review", "Human-in-the-loop safety confirmation gate."),
        ("Step 4", "Deep Crawl", "Sequential traversal, scrolling & telemetry."),
        ("Step 5", "Analyze", "UI Intelligence catalog & route health scoring."),
        ("Step 6", "Security Review", "Passive DAST checks mapped to OWASP Top 10."),
        ("Step 7", "Generate Tests", "Multi-provider AI test planning gateway."),
        ("Step 8", "Execute & Audit", "Controlled Playwright test run & 10-sheet Excel.")
    ]

    card_w = Inches(2.7)
    card_h = Inches(2.2)
    for idx, (s_num, s_title, s_desc) in enumerate(steps):
        row = idx // 4
        col = idx % 4
        x = Inches(0.8) + col * (card_w + Inches(0.25))
        y = Inches(1.8) + row * (card_h + Inches(0.3))

        add_card(slide4, x, y, card_w, card_h, bg_color=C_WHITE)
        tb_s = slide4.shapes.add_textbox(x + Inches(0.15), y + Inches(0.15), card_w - Inches(0.3), card_h - Inches(0.3))
        tf_s = tb_s.text_frame
        tf_s.word_wrap = True

        p_num = tf_s.paragraphs[0]
        p_num.text = s_num.upper()
        p_num.font.size = Pt(11); p_num.font.bold = True; p_num.font.color.rgb = C_BLUE

        p_t = tf_s.add_paragraph()
        p_t.text = s_title
        p_t.font.size = Pt(15); p_t.font.bold = True; p_t.font.color.rgb = C_NAVY; p_t.space_before = Pt(2)

        p_d = tf_s.add_paragraph()
        p_d.text = s_desc
        p_d.font.size = Pt(11); p_d.font.color.rgb = C_SLATE; p_d.space_before = Pt(6)

    # ==========================================
    # SLIDE 5: Crawler Intelligence & Parking Lot
    # ==========================================
    slide5 = prs.slides.add_slide(blank_layout)
    add_header(slide5, "Deterministic Deep Crawling & Destructive Action Parking Lot", "Step 4 & 5 Core Engine")

    add_card(slide5, Inches(0.8), Inches(1.8), Inches(5.6), Inches(5.0))
    tb_c1 = slide5.shapes.add_textbox(Inches(1.0), Inches(2.0), Inches(5.2), Inches(4.6))
    tf_c1 = tb_c1.text_frame
    tf_c1.word_wrap = True
    p = tf_c1.paragraphs[0]
    p.text = "Deep Exploration Capabilities"
    p.font.size = Pt(18); p.font.bold = True; p.font.color.rgb = C_NAVY
    items_c1 = [
        "Viewport Deep Scanner: Progressively scrolls full page and nested overflow containers to trigger lazy-loaded elements.",
        "Menu Overlay Engine: Opens sidebars, dropdowns, and context menus, inspects items, and dismisses cleanly via Escape key.",
        "Sequential Module Traversal: Explores one module at a time, restoring scroll positions and isolating test states.",
        "Atomic Checkpointing: Writes durable checkpoint before and after every route, preventing data loss on crash or pause."
    ]
    for it in items_c1:
        p_it = tf_c1.add_paragraph()
        p_it.text = "• " + it
        p_it.font.size = Pt(13); p_it.font.color.rgb = C_SLATE; p_it.space_before = Pt(8)

    add_card(slide5, Inches(6.9), Inches(1.8), Inches(5.6), Inches(5.0), bg_color=RGBColor(254, 242, 242), border_color=C_RED)
    tb_c2 = slide5.shapes.add_textbox(Inches(7.1), Inches(2.0), Inches(5.2), Inches(4.6))
    tf_c2 = tb_c2.text_frame
    tf_c2.word_wrap = True
    p2 = tf_c2.paragraphs[0]
    p2.text = "Destructive Action Parking Lot"
    p2.font.size = Pt(18); p2.font.bold = True; p2.font.color.rgb = C_RED
    items_c2 = [
        "Strict Classification: Matches high-risk verbs (Delete, Remove, Destroy, Terminate, Purchase, Pay, Reset, Cancel).",
        "Zero-Click Guarantee: The autonomous crawler NEVER clicks parked actions during scanning.",
        "Reviewable Catalog: Parked actions are recorded with screenshot evidence, selector signatures, and exclusion rationales.",
        "Human Confirmation: Actions remain locked until explicit manual human authorization is granted."
    ]
    for it in items_c2:
        p_it = tf_c2.add_paragraph()
        p_it.text = "🛡 " + it
        p_it.font.size = Pt(13); p_it.font.color.rgb = C_SLATE; p_it.space_before = Pt(8)

    # ==========================================
    # SLIDE 6: UI Intelligence & Route Health
    # ==========================================
    slide6 = prs.slides.add_slide(blank_layout)
    add_header(slide6, "UI Intelligence Catalog & 12-Attribute DOM Fingerprinting", "Step 5 Analysis Engine")

    # 12 Attributes Box
    add_card(slide6, Inches(0.8), Inches(1.8), Inches(6.8), Inches(5.0))
    tb_attr = slide6.shapes.add_textbox(Inches(1.0), Inches(2.0), Inches(6.4), Inches(4.6))
    tf_attr = tb_attr.text_frame
    tf_attr.word_wrap = True
    p = tf_attr.paragraphs[0]
    p.text = "12-Attribute Resilient Element Registry"
    p.font.size = Pt(18); p.font.bold = True; p.font.color.rgb = C_NAVY

    attrs = [
        "1. Deterministic Element ID (SHA-256 fingerprint)",
        "2. HTML Tag Name (button, input, select, a)",
        "3. WAI-ARIA Role (button, textbox, combobox)",
        "4. Accessible Name & Visible Text",
        "5. Unique ID Attribute & Name Attribute",
        "6. Test Attributes (data-testid, data-cy, qa-id)",
        "7. Resilient CSS Selector & Scored XPath",
        "8. Bounding Box Dimensions (x, y, width, height)",
        "9. Visibility State & Container Hierarchy",
        "10. Selector Confidence Score (1-100)"
    ]
    for a in attrs:
        p_a = tf_attr.add_paragraph()
        p_a.text = "✔ " + a
        p_a.font.size = Pt(12); p_a.font.color.rgb = C_SLATE; p_a.space_before = Pt(3)

    # Route Health Box
    add_card(slide6, Inches(8.0), Inches(1.8), Inches(4.5), Inches(5.0))
    tb_rh = slide6.shapes.add_textbox(Inches(8.2), Inches(2.0), Inches(4.1), Inches(4.6))
    tf_rh = tb_rh.text_frame
    tf_rh.word_wrap = True
    p_rh = tf_rh.paragraphs[0]
    p_rh.text = "Route Health Classification"
    p_rh.font.size = Pt(18); p_rh.font.bold = True; p_rh.font.color.rgb = C_NAVY

    cats = [
        ("WORKING (Score >= 70)", "High DOM stability, responsive interactions, zero console errors, full element accessibility.", C_EMERALD),
        ("PARTIAL (Score 40-69)", "Minor interaction warnings, slow API responses, or benign redirects.", C_AMBER),
        ("NON-WORKING (Score < 40)", "Blank screens, 4xx/5xx HTTP errors, fatal JS exceptions, or broken navigation.", C_RED)
    ]
    for c_title, c_desc, col in cats:
        p_ct = tf_rh.add_paragraph()
        p_ct.text = c_title
        p_ct.font.size = Pt(14); p_ct.font.bold = True; p_ct.font.color.rgb = col; p_ct.space_before = Pt(10)
        p_cd = tf_rh.add_paragraph()
        p_cd.text = c_desc
        p_cd.font.size = Pt(11); p_cd.font.color.rgb = C_SLATE; p_cd.space_before = Pt(2)

    # ==========================================
    # SLIDE 7: Safe Passive Security DAST Assessment
    # ==========================================
    slide7 = prs.slides.add_slide(blank_layout)
    add_header(slide7, "Safe Basic Security Scan & OWASP Top 10 (2021) Mapping", "Passive DAST Engine")

    add_card(slide7, Inches(0.8), Inches(1.8), Inches(11.7), Inches(5.0))
    tb_sec = slide7.shapes.add_textbox(Inches(1.0), Inches(2.0), Inches(11.3), Inches(4.6))
    tf_sec = tb_sec.text_frame
    tf_sec.word_wrap = True

    p_s_top = tf_sec.paragraphs[0]
    p_s_top.text = "Authorized, Low-Impact Assessment — Zero Destructive Probing"
    p_s_top.font.size = Pt(18); p_s_top.font.bold = True; p_s_top.font.color.rgb = C_BLUE

    sec_checks = [
        ("Security Headers (A05:2021)", "Verifies CSP (frame-ancestors), HSTS, X-Content-Type-Options: nosniff, Referrer-Policy, and Permissions-Policy."),
        ("Cookie Security Flags (A07:2021)", "Validates Secure, HttpOnly, and SameSite attributes on all Set-Cookie headers."),
        ("Mixed Content Detection (A02:2021)", "Identifies unencrypted HTTP assets, scripts, or form submissions on HTTPS pages."),
        ("CORS Misconfiguration (A05:2021)", "Flags wildcard Access-Control-Allow-Origin with credentials or reflected probe origins."),
        ("Sensitive Data Disclosure (A02/A05)", "Detects exposed private keys, AWS/GitHub tokens, JWTs in bodies, database strings, and source maps."),
        ("Error Disclosure (A05:2021)", "Catches verbose stack traces, server exception dumps, and debug toolbars in responses."),
        ("Authorization Boundaries (A01:2021)", "Observes whether protected routes return 200 without valid authentication tokens."),
        ("Inert Reflection Canary (A03:2021)", "Injects safe alphanumeric marker (pione_<uuid>) to test output encoding without exploit payloads.")
    ]
    for title, desc in sec_checks:
        p_chk = tf_sec.add_paragraph()
        p_chk.text = f"• {title}: {desc}"
        p_chk.font.size = Pt(12); p_chk.font.color.rgb = C_SLATE; p_chk.space_before = Pt(4)

    # ==========================================
    # SLIDE 8: Multi-Provider AI Test Planning
    # ==========================================
    slide8 = prs.slides.add_slide(blank_layout)
    add_header(slide8, "Grounded Multi-Provider AI Test Planning Gateway", "Step 7 Test Synthesis")

    col_w8 = Inches(2.15)
    gap8 = Inches(0.2)
    top8 = Inches(1.8)
    h8 = Inches(4.8)

    providers = [
        ("Deterministic", "100% Offline Rule Engine", "Zero cloud tokens or costs. Synthesizes standard functional navigation & element assertion tests deterministically.", C_SLATE),
        ("Ollama", "Local Open-Weights Models", "Local inference via Llama 3, Mistral, Qwen 2.5 Coder. 100% data confidentiality for air-gapped environments.", C_BLUE),
        ("Google Gemini", "Gemini Pro & Flash API", "High-speed multimodal understanding with structured output schema enforcement.", C_CYAN),
        ("Anthropic Claude", "Claude 3.5 Sonnet", "Deep reasoning for complex multi-step user scenarios and edge case generation.", C_EMERALD),
        ("OpenAI", "GPT-4o & GPT-4o-mini", "Robust test scenario generation with JSON schema validation and persona simulation.", C_AMBER)
    ]

    for idx, (p_name, p_sub, p_desc, p_col) in enumerate(providers):
        x = Inches(0.8) + idx * (col_w8 + gap8)
        add_card(slide8, x, top8, col_w8, h8)
        tb_p = slide8.shapes.add_textbox(x + Inches(0.1), top8 + Inches(0.15), col_w8 - Inches(0.2), h8 - Inches(0.3))
        tf_p = tb_p.text_frame
        tf_p.word_wrap = True

        p_h = tf_p.paragraphs[0]
        p_h.text = p_name
        p_h.font.size = Pt(16); p_h.font.bold = True; p_h.font.color.rgb = p_col

        p_s = tf_p.add_paragraph()
        p_s.text = p_sub
        p_s.font.size = Pt(11); p_s.font.bold = True; p_s.font.color.rgb = C_MUTED; p_s.space_before = Pt(2)

        p_d = tf_p.add_paragraph()
        p_d.text = p_desc
        p_d.font.size = Pt(11); p_d.font.color.rgb = C_SLATE; p_d.space_before = Pt(8)

    # ==========================================
    # SLIDE 9: Controlled Execution & Evidence
    # ==========================================
    slide9 = prs.slides.add_slide(blank_layout)
    add_header(slide9, "Controlled Test Execution & Per-Step Screenshot Evidence", "Step 8 Test Runner")

    add_card(slide9, Inches(0.8), Inches(1.8), Inches(5.6), Inches(5.0))
    tb_e1 = slide9.shapes.add_textbox(Inches(1.0), Inches(2.0), Inches(5.2), Inches(4.6))
    tf_e1 = tb_e1.text_frame
    tf_e1.word_wrap = True
    p = tf_e1.paragraphs[0]
    p.text = "Audit-Grade Execution Telemetry"
    p.font.size = Pt(18); p.font.bold = True; p.font.color.rgb = C_NAVY
    items_e1 = [
        "Playwright Execution Engine: Drives real browser contexts with restored authenticated session state.",
        "Locator Fallback Hierarchy: Executes tests using Role -> ARIA -> Text -> CSS -> XPath.",
        "Per-Step Locator Recording: Logs the exact selector engine that satisfied the step.",
        "Single-Step Rerun: Fast troubleshooting of individual steps without re-executing entire regression suite."
    ]
    for it in items_e1:
        p_it = tf_e1.add_paragraph()
        p_it.text = "✔ " + it
        p_it.font.size = Pt(13); p_it.font.color.rgb = C_SLATE; p_it.space_before = Pt(8)

    add_card(slide9, Inches(6.9), Inches(1.8), Inches(5.6), Inches(5.0))
    tb_e2 = slide9.shapes.add_textbox(Inches(7.1), Inches(2.0), Inches(5.2), Inches(4.6))
    tf_e2 = tb_e2.text_frame
    tf_e2.word_wrap = True
    p2 = tf_e2.paragraphs[0]
    p2.text = "Evidence & Diagnostic Capture"
    p2.font.size = Pt(18); p2.font.bold = True; p2.font.color.rgb = C_NAVY
    items_e2 = [
        "Per-Step Screenshots: Automatically captures full-resolution PNG images ({tc_id}_{step_id}.png).",
        "Inline Evidence Modal: Click any test step thumbnail in the dashboard to review screenshot evidence.",
        "Console Log Ingestion: Captures uncaught JavaScript errors, warnings, and unhandled promise rejections.",
        "Network Failure Telemetry: Correlates failing steps with failed HTTP requests (4xx, 5xx, or network drops)."
    ]
    for it in items_e2:
        p_it = tf_e2.add_paragraph()
        p_it.text = "✔ " + it
        p_it.font.size = Pt(13); p_it.font.color.rgb = C_SLATE; p_it.space_before = Pt(8)

    # ==========================================
    # SLIDE 10: 10-Sheet Audit-Ready Excel Workbook
    # ==========================================
    slide10 = prs.slides.add_slide(blank_layout)
    add_header(slide10, "Standard 10-Sheet Enterprise Audit-Ready Workbook", "Reporting & Compliance")

    sheets = [
        ("1. working_routes", "Routes with health score >= 70, verified DOM stability, and 0 console errors."),
        ("2. partial_routes", "Routes with score 40-69, minor performance warnings, or benign redirects."),
        ("3. non_working_routes", "Routes with score < 40, blank screens, 4xx/5xx HTTP errors, or fatal JS faults."),
        ("4. menu_hierarchy", "Complete hierarchical site structure, module trees, and parent-child routes."),
        ("5. element_map", "Full 12-attribute interactive element inventory with selector quality scoring."),
        ("6. parking_lot_actions", "Segregated destructive buttons requiring human authorization prior to execution."),
        ("7. scan_events", "Deterministic, timestamped event log of every crawler and interaction transition."),
        ("8. test_cases", "Generated test specifications, scenario descriptions, tags, and step sequences."),
        ("9. execution_results", "Execution outcomes, locators used, error logs, and screenshot links."),
        ("10. coverage_gaps", "Uncovered user requirements, missing elements, and deferred test paths.")
    ]

    add_card(slide10, Inches(0.8), Inches(1.8), Inches(11.7), Inches(5.0))
    tb_sh = slide10.shapes.add_textbox(Inches(1.0), Inches(2.0), Inches(11.3), Inches(4.6))
    tf_sh = tb_sh.text_frame
    tf_sh.word_wrap = True

    p = tf_sh.paragraphs[0]
    p.text = "Audit-Ready .XLSX Architecture with Frozen Panes & Formula Sanitization"
    p.font.size = Pt(16); p.font.bold = True; p.font.color.rgb = C_BLUE

    for s_title, s_desc in sheets:
        p_s = tf_sh.add_paragraph()
        p_s.text = f"• {s_title}: {s_desc}"
        p_s.font.size = Pt(11.5); p_s.font.color.rgb = C_SLATE; p_s.space_before = Pt(3)

    # ==========================================
    # SLIDE 11: Session Resiliency & In-Place Renewal
    # ==========================================
    slide11 = prs.slides.add_slide(blank_layout)
    add_header(slide11, "In-Place Session Lifecycle Protection (/session/refresh)", "Enterprise Session Resiliency")

    add_card(slide11, Inches(0.8), Inches(1.8), Inches(11.7), Inches(5.0), bg_color=RGBColor(240, 253, 244), border_color=C_EMERALD)
    tb_sess = slide11.shapes.add_textbox(Inches(1.0), Inches(2.0), Inches(11.3), Inches(4.6))
    tf_sess = tb_sess.text_frame
    tf_sess.word_wrap = True

    p_sess_t = tf_sess.paragraphs[0]
    p_sess_t.text = "Solving the 2-Hour Test Run Expiry Problem"
    p_sess_t.font.size = Pt(18); p_sess_t.font.bold = True; p_sess_t.font.color.rgb = C_EMERALD

    sess_points = [
        "Proactive Expiry Detection: Monitors token validity and detects login redirects or visible password fields.",
        "Zero Data Loss Guarantee: When a session expires, the run status pauses—it is NEVER discarded.",
        "In-Place Refresh Modal: The UI prompts the user to re-enter credentials in a non-blocking modal dialog.",
        "Run Context Continuity: The server updates the opaque server-side storage state and binds it to the exact same Run ID.",
        "Seamless Traversal Resume: The crawler or regression suite resumes from the active checkpoint without restarting."
    ]
    for pt in sess_points:
        p_pt = tf_sess.add_paragraph()
        p_pt.text = "✔ " + pt
        p_pt.font.size = Pt(13.5); p_pt.font.color.rgb = C_SLATE; p_pt.space_before = Pt(10)

    # ==========================================
    # SLIDE 12: Production Verification & Summary
    # ==========================================
    slide12 = prs.slides.add_slide(blank_layout)
    add_header(slide12, "Production Verification Benchmarks & Conclusion", "Verification Summary")

    # Metrics Grid
    m_w = Inches(2.7)
    m_h = Inches(1.8)
    metrics = [
        ("167 / 167", "Pytest Suite Passing", C_EMERALD),
        ("0 Flaky Locators", "12-Attribute DOM Fingerprints", C_BLUE),
        ("100% Safe", "Zero Destructive Actions Clicked", C_AMBER),
        ("10 Sheets", "Audit-Ready Excel Workbooks", C_NAVY)
    ]
    for idx, (m_val, m_lbl, m_col) in enumerate(metrics):
        x = Inches(0.8) + idx * (m_w + Inches(0.3))
        add_card(slide12, x, Inches(1.8), m_w, m_h)
        tb_m = slide12.shapes.add_textbox(x, Inches(2.1), m_w, Inches(1.2))
        tf_m = tb_m.text_frame
        p_val = tf_m.paragraphs[0]
        p_val.text = m_val
        p_val.font.size = Pt(28); p_val.font.bold = True; p_val.font.color.rgb = m_col; p_val.alignment = PP_ALIGN.CENTER
        p_lbl = tf_m.add_paragraph()
        p_lbl.text = m_lbl
        p_lbl.font.size = Pt(12); p_lbl.font.color.rgb = C_SLATE; p_lbl.alignment = PP_ALIGN.CENTER

    # Conclusion Card
    add_card(slide12, Inches(0.8), Inches(4.0), Inches(11.7), Inches(2.8))
    tb_c = slide12.shapes.add_textbox(Inches(1.0), Inches(4.2), Inches(11.3), Inches(2.4))
    tf_c = tb_c.text_frame
    tf_c.word_wrap = True
    p_c_head = tf_c.paragraphs[0]
    p_c_head.text = "Summary: Enterprise UI Intelligence Ready for Scale"
    p_c_head.font.size = Pt(18); p_c_head.font.bold = True; p_c_head.font.color.rgb = C_NAVY
    conclusions = [
        "Deterministic Grounding: Playwright provides rock-solid UI discovery and execution; AI provides intelligent scenario planning.",
        "Enterprise Governance: Parking lot safety policies, SSRF IP-level guardrails, and typed DELETE safeguards eliminate business risk.",
        "Lifecycle Resiliency: Checkpoints, resumable runs, and in-place session refresh ensure tests complete reliably.",
        "Turnkey Compliance: Multi-format exports and 10-sheet workbooks satisfy both technical QA and regulatory audit requirements."
    ]
    for c in conclusions:
        p_c = tf_c.add_paragraph()
        p_c.text = "• " + c
        p_c.font.size = Pt(12.5); p_c.font.color.rgb = C_SLATE; p_c.space_before = Pt(4)

    # Save presentation
    prs.save(str(pptx_path))
    print(f"[SUCCESS] Presentation generated successfully at: {pptx_path}")

if __name__ == "__main__":
    create_deck()
