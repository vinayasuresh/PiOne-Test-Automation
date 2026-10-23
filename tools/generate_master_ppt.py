"""
Comprehensive script to generate the 20-slide Master Enterprise & Technical Presentation
for PiOne Testmate, embedding real high-DPI UI screenshots, architecture blueprints,
data models, security matrices, and competitive ROI analysis.
"""
from pathlib import Path
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE

def build_master_deck():
    workspace_root = Path(__file__).resolve().parents[1]
    presentation_dir = workspace_root / "presentation"
    img_dir = presentation_dir / "images"
    presentation_dir.mkdir(parents=True, exist_ok=True)

    # We will output to both presentation/ and root directory
    deck_paths = [
        presentation_dir / "pione_testmate_presentation.pptx",
        workspace_root / "PiOne_Testmate_Presentation.pptx",
    ]

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    # Brand Colors
    C_NAVY_DARK = RGBColor(11, 15, 25)      # #0B0F19 (Deep background)
    C_NAVY = RGBColor(15, 23, 42)          # #0F172A
    C_SLATE = RGBColor(30, 41, 59)         # #1E293B (Card surface)
    C_SLATE_LIGHT = RGBColor(51, 65, 85)   # #334155
    C_BLUE = RGBColor(37, 99, 235)         # #2563EB (Brand primary)
    C_CYAN = RGBColor(6, 182, 212)         # #06B6D4 (Accent cyan)
    C_EMERALD = RGBColor(16, 185, 129)     # #10B981 (Success/Safety)
    C_AMBER = RGBColor(245, 158, 11)       # #F59E0B (Warning/Notice)
    C_PURPLE = RGBColor(139, 92, 246)      # #8B5CF6 (AI Intelligence)
    C_WHITE = RGBColor(255, 255, 255)
    C_BG_LIGHT = RGBColor(248, 250, 252)   # #F8FAFC
    C_MUTED = RGBColor(100, 116, 139)      # #64748B
    C_BORDER = RGBColor(226, 232, 240)     # #E2E8F0
    C_DARK_BORDER = RGBColor(51, 65, 85)

    blank_layout = prs.slide_layouts[6]

    # Helper: Set slide background color
    def set_slide_bg(slide, color):
        bg = slide.background
        fill = bg.fill
        fill.solid()
        fill.fore_color.rgb = color

    # Helper: Header Banner
    def add_header(slide, title_text, category_text="PIONE TESTMATE ENTERPRISE", dark=False):
        # Category / Step Tag
        cat_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.3))
        tf_cat = cat_box.text_frame
        tf_cat.word_wrap = True
        p_cat = tf_cat.paragraphs[0]
        p_cat.text = category_text.upper()
        p_cat.font.size = Pt(10)
        p_cat.font.bold = True
        p_cat.font.color.rgb = C_CYAN if dark else C_BLUE

        # Main Title
        title_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.68), Inches(11.7), Inches(0.65))
        tf = title_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = title_text
        p.font.size = Pt(21)
        p.font.bold = True
        p.font.color.rgb = C_WHITE if dark else C_NAVY

    # Helper: Card container
    def add_card(slide, left, top, width, height, bg_color=C_WHITE, border_color=C_BORDER):
        shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
        shape.fill.solid()
        shape.fill.fore_color.rgb = bg_color
        shape.line.color.rgb = border_color
        shape.line.width = Pt(1.5)
        return shape

    # Helper: Split slide with screenshot on right
    def add_split_slide(slide, title, category, headline, points, guarantee_text, img_name, caption):
        set_slide_bg(slide, C_BG_LIGHT)
        add_header(slide, title, category, dark=False)

        # Left Column: Value Proposition & Feature Card
        card_left = Inches(0.8)
        card_top = Inches(1.5)
        card_w = Inches(5.1)
        card_h = Inches(5.4)

        add_card(slide, card_left, card_top, card_w, card_h, C_WHITE, C_BORDER)
        tb = slide.shapes.add_textbox(card_left + Inches(0.25), card_top + Inches(0.25), card_w - Inches(0.5), card_h - Inches(0.5))
        tf = tb.text_frame
        tf.word_wrap = True

        # Headline
        p_head = tf.paragraphs[0]
        p_head.text = headline
        p_head.font.size = Pt(15.5)
        p_head.font.bold = True
        p_head.font.color.rgb = C_NAVY

        # Subhead / Value Callout
        p_val = tf.add_paragraph()
        p_val.text = "🎯 Executive Value:"
        p_val.font.size = Pt(11)
        p_val.font.bold = True
        p_val.font.color.rgb = C_BLUE
        p_val.space_before = Pt(8)

        # Bullets
        for pt in points:
            p_b = tf.add_paragraph()
            p_b.text = "✔ " + pt
            p_b.font.size = Pt(11)
            p_b.font.color.rgb = C_SLATE
            p_b.space_before = Pt(6)

        # Enterprise Guarantee Badge
        if guarantee_text:
            p_g = tf.add_paragraph()
            p_g.text = f"🛡 Enterprise Guarantee: {guarantee_text}"
            p_g.font.size = Pt(10.5)
            p_g.font.bold = True
            p_g.font.color.rgb = C_EMERALD
            p_g.space_before = Pt(12)

        # Right Column: Screenshot Visual Frame
        img_left = Inches(6.15)
        img_top = Inches(1.5)
        img_w = Inches(6.4)
        img_h = Inches(5.0)

        # Visual Shadow/Border Frame
        frame = add_card(slide, img_left, img_top, img_w, img_h, C_NAVY, C_BORDER)
        img_path = img_dir / img_name
        if img_path.exists():
            slide.shapes.add_picture(
                str(img_path),
                img_left + Inches(0.08),
                img_top + Inches(0.08),
                img_w - Inches(0.16),
                img_h - Inches(0.16)
            )

        # Caption
        cap_box = slide.shapes.add_textbox(img_left, img_top + img_h + Inches(0.08), img_w, Inches(0.3))
        p_cap = cap_box.text_frame.paragraphs[0]
        p_cap.text = f"LIVE PLATFORM CAPTURE: {caption.upper()} [1440x900 @ 2x HI-DPI]"
        p_cap.font.size = Pt(9)
        p_cap.font.color.rgb = C_MUTED
        p_cap.font.bold = True

    # Helper: Native Table Slide
    def add_table_slide(slide, title, category, headers, rows, col_widths, note=None):
        set_slide_bg(slide, C_BG_LIGHT)
        add_header(slide, title, category, dark=False)

        top_pos = Inches(1.5)
        left_pos = Inches(0.8)
        table_w = sum(col_widths)
        table_h = Inches(0.5 + 0.45 * len(rows))

        table_shape = slide.shapes.add_table(len(rows) + 1, len(headers), left_pos, top_pos, table_w, table_h)
        tbl = table_shape.table

        # Set column widths
        for i, w in enumerate(col_widths):
            tbl.columns[i].width = w

        # Header Row
        for i, h in enumerate(headers):
            cell = tbl.cell(0, i)
            cell.text = h
            cell.fill.solid()
            cell.fill.fore_color.rgb = C_NAVY
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            p = cell.text_frame.paragraphs[0]
            p.font.size = Pt(11)
            p.font.bold = True
            p.font.color.rgb = C_WHITE
            p.alignment = PP_ALIGN.LEFT

        # Data Rows
        for r_idx, row in enumerate(rows):
            is_even = (r_idx % 2 == 0)
            row_bg = C_WHITE if is_even else RGBColor(241, 245, 249)
            for c_idx, val in enumerate(row):
                cell = tbl.cell(r_idx + 1, c_idx)
                cell.text = str(val)
                cell.fill.solid()
                cell.fill.fore_color.rgb = row_bg
                cell.vertical_anchor = MSO_ANCHOR.MIDDLE
                p = cell.text_frame.paragraphs[0]
                p.font.size = Pt(10)
                p.font.color.rgb = C_SLATE
                if c_idx == 0:
                    p.font.bold = True
                    p.font.color.rgb = C_BLUE

        if note:
            nb = slide.shapes.add_textbox(left_pos, top_pos + table_h + Inches(0.2), table_w, Inches(0.4))
            p_note = nb.text_frame.paragraphs[0]
            p_note.text = f"ℹ Note: {note}"
            p_note.font.size = Pt(9.5)
            p_note.font.italic = True
            p_note.font.color.rgb = C_MUTED

    # -------------------------------------------------------------
    # SLIDE 1: Title Slide (Dark Theme)
    # -------------------------------------------------------------
    s1 = prs.slides.add_slide(blank_layout)
    set_slide_bg(s1, C_NAVY_DARK)

    # Brand Title Pill
    pill = add_card(s1, Inches(0.8), Inches(1.2), Inches(2.2), Inches(0.4), C_BLUE, C_BLUE)
    tb_pill = s1.shapes.add_textbox(Inches(0.8), Inches(1.2), Inches(2.2), Inches(0.4))
    p_p = tb_pill.text_frame.paragraphs[0]
    p_p.text = "ENTERPRISE PLATFORM"
    p_p.font.size = Pt(10)
    p_p.font.bold = True
    p_p.font.color.rgb = C_WHITE
    p_p.alignment = PP_ALIGN.CENTER

    # Main Title
    tb_t = s1.shapes.add_textbox(Inches(0.8), Inches(1.8), Inches(11.7), Inches(1.5))
    p_t = tb_t.text_frame.paragraphs[0]
    p_t.text = "PiOne Testmate"
    p_t.font.size = Pt(44)
    p_t.font.bold = True
    p_t.font.color.rgb = C_WHITE

    p_sub = tb_t.text_frame.add_paragraph()
    p_sub.text = "Autonomous Enterprise Web QA & Low-Impact DAST Security Platform"
    p_sub.font.size = Pt(20)
    p_sub.font.color.rgb = C_CYAN
    p_sub.space_before = Pt(8)

    # 3 Value Cards
    c1 = add_card(s1, Inches(0.8), Inches(3.8), Inches(3.6), Inches(2.8), C_SLATE, C_DARK_BORDER)
    tb_c1 = s1.shapes.add_textbox(Inches(1.0), Inches(4.0), Inches(3.2), Inches(2.4))
    tf1 = tb_c1.text_frame
    tf1.word_wrap = True
    tf1.paragraphs[0].text = "⚡ Zero-Script Setup"
    tf1.paragraphs[0].font.size = Pt(16)
    tf1.paragraphs[0].font.bold = True
    tf1.paragraphs[0].font.color.rgb = C_CYAN
    p1 = tf1.add_paragraph()
    p1.text = "Autonomous SPA & SSR route discovery without writing fragile manual selector scripts. Cut test authoring cycles from weeks to minutes."
    p1.font.size = Pt(11)
    p1.font.color.rgb = RGBColor(203, 213, 225)
    p1.space_before = Pt(8)

    c2 = add_card(s1, Inches(4.8), Inches(3.8), Inches(3.6), Inches(2.8), C_SLATE, C_DARK_BORDER)
    tb_c2 = s1.shapes.add_textbox(Inches(5.0), Inches(4.0), Inches(3.2), Inches(2.4))
    tf2 = tb_c2.text_frame
    tf2.word_wrap = True
    tf2.paragraphs[0].text = "🛡️ Production-Safe DAST"
    tf2.paragraphs[0].font.size = Pt(16)
    tf2.paragraphs[0].font.bold = True
    tf2.paragraphs[0].font.color.rgb = C_EMERALD
    p2 = tf2.add_paragraph()
    p2.text = "Passive OWASP Top 10 security auditing across discovered routes. Detects CSP, HSTS, cookie flag, and unauth access flaws without destructive payloads."
    p2.font.size = Pt(11)
    p2.font.color.rgb = RGBColor(203, 213, 225)
    p2.space_before = Pt(8)

    c3 = add_card(s1, Inches(8.8), Inches(3.8), Inches(3.6), Inches(2.8), C_SLATE, C_DARK_BORDER)
    tb_c3 = s1.shapes.add_textbox(Inches(9.0), Inches(4.0), Inches(3.2), Inches(2.4))
    tf3 = tb_c3.text_frame
    tf3.word_wrap = True
    tf3.paragraphs[0].text = "🎯 Enterprise Resilience"
    tf3.paragraphs[0].font.size = Pt(16)
    tf3.paragraphs[0].font.bold = True
    tf3.paragraphs[0].font.color.rgb = C_PURPLE
    p3 = tf3.add_paragraph()
    p3.text = "SQLite / PostgreSQL relational persistence, safe checkpoint resume, visual regression baselines, and multi-provider AI model freedom."
    p3.font.size = Pt(11)
    p3.font.color.rgb = RGBColor(203, 213, 225)
    p3.space_before = Pt(8)

    # Footer note
    tb_foot = s1.shapes.add_textbox(Inches(0.8), Inches(6.8), Inches(11.7), Inches(0.4))
    p_f = tb_foot.text_frame.paragraphs[0]
    p_f.text = "CONFIDENTIAL & PROPRIETARY — PIONE TECHNOLOGIES LLP — PLATFORM RELEASE V2.1 (OCTOBER 2026)"
    p_f.font.size = Pt(9)
    p_f.font.color.rgb = C_MUTED

    # -------------------------------------------------------------
    # SLIDE 2: The Enterprise QA Dilemma
    # -------------------------------------------------------------
    s2 = prs.slides.add_slide(blank_layout)
    set_slide_bg(s2, C_BG_LIGHT)
    add_header(s2, "The Enterprise Web QA Dilemma", "MARKET ANALYSIS & PROBLEM STATEMENT")

    c2_1 = add_card(s2, Inches(0.8), Inches(1.5), Inches(3.6), Inches(5.4), C_WHITE, RGBColor(254, 202, 202))
    tb_c2_1 = s2.shapes.add_textbox(Inches(1.0), Inches(1.8), Inches(3.2), Inches(4.8))
    tf2_1 = tb_c2_1.text_frame
    tf2_1.word_wrap = True
    tf2_1.paragraphs[0].text = "⏳ Fragile Script Maintenance"
    tf2_1.paragraphs[0].font.size = Pt(16)
    tf2_1.paragraphs[0].font.bold = True
    tf2_1.paragraphs[0].font.color.rgb = RGBColor(220, 38, 38)
    bullets1 = [
        "Teams spend 40% to 60% of test engineering sprints repairing broken XPath and CSS locators.",
        "Minor UI redesigns silently break test suites, leading to release delays and QA friction.",
        "High barrier to entry: requires dedicated automation engineers to maintain large repositories.",
        "Result: Test coverage stagnates while applications rapidly evolve."
    ]
    for b in bullets1:
        p = tf2_1.add_paragraph()
        p.text = "✖ " + b
        p.font.size = Pt(10.5)
        p.font.color.rgb = C_SLATE
        p.space_before = Pt(8)

    c2_2 = add_card(s2, Inches(4.8), Inches(1.5), Inches(3.6), Inches(5.4), C_WHITE, RGBColor(254, 243, 199))
    tb_c2_2 = s2.shapes.add_textbox(Inches(5.0), Inches(1.8), Inches(3.2), Inches(4.8))
    tf2_2 = tb_c2_2.text_frame
    tf2_2.word_wrap = True
    tf2_2.paragraphs[0].text = "💣 Destructive Autonomous Bots"
    tf2_2.paragraphs[0].font.size = Pt(16)
    tf2_2.paragraphs[0].font.bold = True
    tf2_2.paragraphs[0].font.color.rgb = C_AMBER
    bullets2 = [
        "Unbounded AI agents inadvertently click delete buttons, cancel accounts, or submit orders.",
        "Black-box crawlers execute arbitrary external links, leading to SSRF and security alerts.",
        "Non-deterministic execution: LLM hallucinations generate tests for elements that don't exist.",
        "Result: Enterprise security teams ban autonomous AI tools from staging and production."
    ]
    for b in bullets2:
        p = tf2_2.add_paragraph()
        p.text = "✖ " + b
        p.font.size = Pt(10.5)
        p.font.color.rgb = C_SLATE
        p.space_before = Pt(8)

    c2_3 = add_card(s2, Inches(8.8), Inches(1.5), Inches(3.6), Inches(5.4), C_WHITE, RGBColor(167, 243, 208))
    tb_c2_3 = s2.shapes.add_textbox(Inches(9.0), Inches(1.8), Inches(3.2), Inches(4.8))
    tf2_3 = tb_c2_3.text_frame
    tf2_3.word_wrap = True
    tf2_3.paragraphs[0].text = "🚀 The PiOne Testmate Solution"
    tf2_3.paragraphs[0].font.size = Pt(16)
    tf2_3.paragraphs[0].font.bold = True
    tf2_3.paragraphs[0].font.color.rgb = C_EMERALD
    bullets3 = [
        "Deterministic Playwright Scanner First: The browser is strictly driven by deterministic code.",
        "Grounded AI Second: LLMs only generate tests bounded to elements in the discovered DOM registry.",
        "Safety Parking Lot: Destructive actions quarantined for explicit human operator confirmation.",
        "Zero Lock-in: Emits pure standard pytest code with full relational checkpoint persistence."
    ]
    for b in bullets3:
        p = tf2_3.add_paragraph()
        p.text = "✔ " + b
        p.font.size = Pt(10.5)
        p.font.color.rgb = C_SLATE
        p.space_before = Pt(8)

    # -------------------------------------------------------------
    # SLIDE 3: Core Philosophy & Value Pillars
    # -------------------------------------------------------------
    s3 = prs.slides.add_slide(blank_layout)
    set_slide_bg(s3, C_BG_LIGHT)
    add_header(s3, "Core Philosophy & Enterprise Value Pillars", "SYSTEM ARCHITECTURE & DESIGN PRINCIPLES")

    # Guiding Principle Banner
    gp = add_card(s3, Inches(0.8), Inches(1.5), Inches(11.7), Inches(1.1), C_NAVY, C_NAVY)
    tb_gp = s3.shapes.add_textbox(Inches(1.0), Inches(1.6), Inches(11.3), Inches(0.9))
    tf_gp = tb_gp.text_frame
    tf_gp.word_wrap = True
    p_gp1 = tf_gp.paragraphs[0]
    p_gp1.text = "GUIDING PRINCIPLE: DETERMINISTIC SCANNER FIRST, GROUNDED AI INTELLIGENCE SECOND"
    p_gp1.font.size = Pt(12)
    p_gp1.font.bold = True
    p_gp1.font.color.rgb = C_CYAN
    p_gp2 = tf_gp.add_paragraph()
    p_gp2.text = "Playwright deterministically owns browser exploration, DOM extraction, and execution. Artificial Intelligence models (Gemini, Claude, GPT, Ollama) are strictly restricted to synthesizing structured test scenarios from registered elements. AI never directly touches the browser."
    p_gp2.font.size = Pt(10.5)
    p_gp2.font.color.rgb = RGBColor(226, 232, 240)
    p_gp2.space_before = Pt(4)

    # 4 Pillar Cards
    pillars = [
        ("🛡️ SSRF Guardrails", "Target origin allowlisting and strict network blocking of private IP ranges (10.0.0.0/8, 127.0.0.1, 169.254.169.254).", C_BLUE),
        ("🎯 12-Attr Fingerprint", "Multi-attribute selector scoring (Role > ARIA > ID > Text > CSS > XPath) guaranteeing self-healing locator resilience.", C_CYAN),
        ("🛑 Safety Parking Lot", "Dangerous keywords (delete, cancel, reset, buy) quarantined into a governance review list requiring human sign-off.", C_EMERALD),
        ("🔄 In-Place Session Refresh", "Expired sessions are renewed via /session/refresh without dropping discovered route graphs, checkpoints, or active runs.", C_PURPLE)
    ]
    for i, (p_title, p_desc, p_col) in enumerate(pillars):
        col_left = Inches(0.8 + i * 2.98)
        c_pill = add_card(s3, col_left, Inches(2.8), Inches(2.76), Inches(4.1), C_WHITE, C_BORDER)
        tb_p = s3.shapes.add_textbox(col_left + Inches(0.15), Inches(3.0), Inches(2.46), Inches(3.7))
        tf_p = tb_p.text_frame
        tf_p.word_wrap = True
        tf_p.paragraphs[0].text = p_title
        tf_p.paragraphs[0].font.size = Pt(14)
        tf_p.paragraphs[0].font.bold = True
        tf_p.paragraphs[0].font.color.rgb = p_col
        p = tf_p.add_paragraph()
        p.text = p_desc
        p.font.size = Pt(10.5)
        p.font.color.rgb = C_SLATE
        p.space_before = Pt(8)

    # -------------------------------------------------------------
    # SLIDE 4: Unified System Architecture Blueprint
    # -------------------------------------------------------------
    s4 = prs.slides.add_slide(blank_layout)
    set_slide_bg(s4, C_BG_LIGHT)
    add_header(s4, "Unified System Architecture Blueprint", "4-TIER TECHNICAL OVERVIEW")

    tiers = [
        ("Tier 1: Presentation & UI", "Tailwind CSS SPA", [
            "8-Step Responsive Stepper Interface",
            "Persistent Active-Run HUD Bar",
            "Dual-Transport WebSocket / Polling Telemetry",
            "Interactive Element & Security Dossier Viewers",
            "Saved Runs, Regression Suites & Baseline Hubs"
        ], C_BLUE),
        ("Tier 2: API Gateway", "FastAPI Asynchronous Engine", [
            "/scan & /discover-modules Orchestration",
            "/scan/control Live Pause / Resume / Cancel",
            "/runs/{id}/security Passive DAST Scanner",
            "/generate-tests Multi-Provider AI Gateway",
            "/execute-tests High-Speed Controlled Runner"
        ], C_CYAN),
        ("Tier 3: Core Engine", "Playwright Automation Core", [
            "SecurityGuard (SSRF & Boundary Containment)",
            "ViewportDeepScanner & 12-Attr Fingerprinting",
            "ActionClassifier (Safe vs. Parking-Lot)",
            "AuthLifecycleService & Session Key Storage",
            "Browser Session Recovery & Reconnect Engine"
        ], C_EMERALD),
        ("Tier 4: Durable Persistence", "Relational Repository", [
            "SQLAlchemy ORM (SQLite / PostgreSQL Support)",
            "RunContext JSON & Memory State Synchronizer",
            "10-Sheet Standardized Excel Audit Workbooks",
            "JUnit XML, JSON Metrics, & HTML Report Store",
            "Perceptual Visual Regression Baseline Archive"
        ], C_PURPLE)
    ]

    for i, (t_name, t_tech, t_items, t_col) in enumerate(tiers):
        col_left = Inches(0.8 + i * 2.98)
        card = add_card(s4, col_left, Inches(1.5), Inches(2.76), Inches(5.4), C_WHITE, C_BORDER)
        tb = s4.shapes.add_textbox(col_left + Inches(0.15), Inches(1.7), Inches(2.46), Inches(5.0))
        tf = tb.text_frame
        tf.word_wrap = True

        tf.paragraphs[0].text = t_name
        tf.paragraphs[0].font.size = Pt(13)
        tf.paragraphs[0].font.bold = True
        tf.paragraphs[0].font.color.rgb = t_col

        p_tech = tf.add_paragraph()
        p_tech.text = t_tech
        p_tech.font.size = Pt(10)
        p_tech.font.bold = True
        p_tech.font.color.rgb = C_MUTED
        p_tech.space_before = Pt(2)

        for item in t_items:
            p = tf.add_paragraph()
            p.text = "• " + item
            p.font.size = Pt(9.5)
            p.font.color.rgb = C_SLATE
            p.space_before = Pt(6)

    # -------------------------------------------------------------
    # SLIDE 5: The End-to-End 8-Step Lifecycle
    # -------------------------------------------------------------
    s5 = prs.slides.add_slide(blank_layout)
    set_slide_bg(s5, C_BG_LIGHT)
    add_header(s5, "The End-to-End 8-Step Testing Lifecycle", "OPERATIONAL PIPELINE & GOVERNANCE GATES")

    steps = [
        ("1. Scan App", "Target specification, credentials containment, and initial SPA/SSR probe.", C_BLUE),
        ("2. Choose Modules", "Semantic clustering of discovered paths into testable functional domains.", C_BLUE),
        ("3. Manual Review", "Human-in-the-loop governance isolating destructive verbs in Parking Lot.", C_AMBER),
        ("4. Crawl Telemetry", "Deep sequential traversal with sub-second WebSocket telemetry HUD.", C_CYAN),
        ("5. Analyze Routes", "Route health classification (Working/Partial/Non-Working) & element map.", C_CYAN),
        ("6. Security DAST", "Passive OWASP Top 10 auditing (CSP, HSTS, cookie flags, unauth access).", C_EMERALD),
        ("7. Generate Tests", "Multi-provider AI planning gateway emitting clean standalone pytest code.", C_PURPLE),
        ("8. Execute Tests", "Controlled headless/headed runner with per-step live assertion streaming.", C_PURPLE),
    ]

    for i, (st_name, st_desc, st_col) in enumerate(steps):
        row = i // 4
        col = i % 4
        c_left = Inches(0.8 + col * 2.98)
        c_top = Inches(1.5 + row * 2.75)
        c_w = Inches(2.76)
        c_h = Inches(2.45)

        add_card(s5, c_left, c_top, c_w, c_h, C_WHITE, C_BORDER)
        tb = s5.shapes.add_textbox(c_left + Inches(0.15), c_top + Inches(0.15), c_w - Inches(0.3), c_h - Inches(0.3))
        tf = tb.text_frame
        tf.word_wrap = True

        tf.paragraphs[0].text = st_name
        tf.paragraphs[0].font.size = Pt(14)
        tf.paragraphs[0].font.bold = True
        tf.paragraphs[0].font.color.rgb = st_col

        p = tf.add_paragraph()
        p.text = st_desc
        p.font.size = Pt(10)
        p.font.color.rgb = C_SLATE
        p.space_before = Pt(6)

        p_gate = tf.add_paragraph()
        p_gate.text = "Gate: Explicit Confirmation"
        p_gate.font.size = Pt(8.5)
        p_gate.font.bold = True
        p_gate.font.color.rgb = C_MUTED
        p_gate.space_before = Pt(8)

    # -------------------------------------------------------------
    # SLIDES 6-16: Core Workflow Steps with UI Screenshots
    # -------------------------------------------------------------
    # Slide 6: Step 1 Scan
    s6 = prs.slides.add_slide(blank_layout)
    add_split_slide(
        s6,
        "Step 1: Scan Application & Session Lifecycle",
        "STEP 1 — INITIATION & TARGET SPECIFICATION",
        "Target Configuration with Strict Session Containment",
        [
            "Single entry point supporting both public applications and authenticated enterprise portals.",
            "Opaque Session Storage: Browser cookies and storage states saved under reports/auth_sessions/<uuid>.json.",
            "Deterministic Mock/Demo Mode enables 100% offline, air-gapped evaluation without network dependencies.",
            "Target Origin Boundary: Enforces strict host validation to prevent inadvertent off-site exploration."
        ],
        "Passwords, session tokens, and local storage keys are never logged in plaintext or exposed to telemetry.",
        "01_scan_application.png",
        "Target URL specification, auth credentials, and offline demo controls"
    )

    # Slide 7: Step 2 Choose Modules
    s7 = prs.slides.add_slide(blank_layout)
    add_split_slide(
        s7,
        "Step 2: Intelligent Module Discovery & Boundary Locking",
        "STEP 2 — SCOPING & SUBDOMAIN ISOLATION",
        "Semantic Route Clustering & Selective Test Scoping",
        [
            "Autonomous Route Clustering: Discovered URLs grouped into logical domains (e.g. Auth, Inventory, Cart, Settings).",
            "Granular Selection: Operators select only the modules affected by recent commits, cutting crawl time by up to 85%.",
            "Dynamic Stale Run Invalidation: Changing module selection transparently flags downstream test suites as needing regeneration.",
            "Boundary Locking: Prevents crawlers from expanding into unwanted subdomains or external OAuth redirect targets."
        ],
        "Crawling and test generation strictly bounded to selected modules; eliminates redundant enterprise test cycles.",
        "02_choose_modules.png",
        "Module selection checkboxes with real-time route count indicators"
    )

    # Slide 8: Step 3 Manual Review
    s8 = prs.slides.add_slide(blank_layout)
    add_split_slide(
        s8,
        "Step 3: Interactive Manual Review & Safety Enclaves",
        "STEP 3 — HUMAN-IN-THE-LOOP GOVERNANCE",
        "Pre-Crawl Risk Triage & Destructive Action Parking Lot",
        [
            "Heuristic Classifier: Automatically flags dangerous endpoints containing keywords like delete, cancel, reset, purchase, logout.",
            "Parking-Lot Enclave: Questionable routes quarantined into a dedicated review table requiring explicit operator sign-off.",
            "Deep-Link Ingestion: Operators can manually inject custom deep operational URLs into the queue before crawling.",
            "Zero Surprise Guarantee: Autonomous crawlers are physically blocked from clicking unapproved destructive buttons."
        ],
        "Zero unintentional destructive database mutations or account terminations during autonomous crawling.",
        "03_manual_review.png",
        "Interactive route confirmation table with parking-lot safety indicators"
    )

    # Slide 9: Step 4 Crawl Progress
    s9 = prs.slides.add_slide(blank_layout)
    add_split_slide(
        s9,
        "Step 4: Real-Time Crawl Telemetry & Bounded Traversal",
        "STEP 4 — TRAVERSAL & RESILIENT TELEMETRY",
        "Sub-Second Live Streaming & Global Active-Run HUD",
        [
            "Dual-Transport Telemetry: High-speed WebSocket streaming with seamless fallback to HTTP polling upon network drops.",
            "Persistent Active-Run HUD: Global top bar displays Target URL, Run ID, Active Module, Current Route, and DB Status.",
            "Operator Controls: Live Pause, Resume, and Graceful End Scan actions available at any moment.",
            "Terminal State Integrity: Completed scans lock controls, stop polling, and cleanly activate 'View Analysis'."
        ],
        "Zero automatic phase transitions; operator retains complete supervisory control throughout exploration.",
        "04_crawl_telemetry.png",
        "Live progress bar, route stream, and active-run persistent HUD"
    )

    # Slide 10: Step 5 Route Health
    s10 = prs.slides.add_slide(blank_layout)
    add_split_slide(
        s10,
        "Step 5: Route Health Catalog & UI Intelligence",
        "STEP 5 — CATALOGING & ROUTE TAXONOMY",
        "Automated Health Derivation & 12-Attribute Element Fingerprinting",
        [
            "Normalized Route Taxonomy: Every page classified into Working, Partial, or Non-Working based on HTTP and client responses.",
            "12-Attribute Element Map: Indexes all interactive buttons, inputs, selects, links, forms, and ARIA accessibility roles.",
            "Instant Triage: Detects 404 dead links, 500 server crashes, and client-side rendering failures before writing tests.",
            "Deep Filtering: Filter discovered elements by module, HTTP status code, interactive role, or URL path."
        ],
        "Summary metric cards strictly reconcile with underlying route records; zero false-zero counts.",
        "05_route_health_catalog.png",
        "Discovered route table, health badges, and element density metrics"
    )

    # Slide 11: Step 6 DAST Security
    s11 = prs.slides.add_slide(blank_layout)
    add_split_slide(
        s11,
        "Step 6: Non-Destructive DAST Security Assessment",
        "STEP 6 — PASSIVE VULNERABILITY AUDITING",
        "OWASP Top 10 (2021) Passive Compliance Auditing",
        [
            "Security Headers Audit: Evaluates Strict-Transport-Security (HSTS), Content-Security-Policy (CSP), and X-Frame-Options.",
            "Cookie Security Review: Flags missing Secure, HttpOnly, and SameSite flags across all established session cookies.",
            "Unauthenticated Access Probing: Tests discovered internal routes without auth headers to identify broken access controls.",
            "CORS & Secret Leakage: Scans client responses for permissive wildcard origins and accidentally exposed tokens."
        ],
        "Low-impact passive inspection only; zero destructive payloads, injection attacks, or denial-of-service risks.",
        "06_security_assessment.png",
        "OWASP finding cards, severity badges, and remediation recommendations"
    )

    # Slide 12: Step 7 AI Planning
    s12 = prs.slides.add_slide(blank_layout)
    add_split_slide(
        s12,
        "Step 7: Multi-Provider AI Test Planning Gateway",
        "STEP 7 — GROUNDED TEST SYNTHESIS",
        "Vendor-Neutral Test Intelligence Without Hallucinations",
        [
            "Model Freedom: Pluggable gateway supports Google Gemini 2.5 Flash, OpenAI GPT-4o, Anthropic Claude 3.5, or Local Ollama.",
            "Zero Hallucination Guarantee: Prompts strictly grounded with elements from the discovered DOM registry.",
            "Clean Code Synthesis: Emits standard Python + Playwright code; no proprietary runtime or vendor lock-in.",
            "Editable Plan Staging: Review, reorder, or customize test scenarios and assertions before generating code."
        ],
        "Generated test files are 100% portable and run in any CI/CD pipeline via standard pytest CLI.",
        "07_ai_test_planning.png",
        "LLM provider selection, scenario generator, and editable test plan"
    )

    # Slide 13: Step 8 Controlled Execution
    s13 = prs.slides.add_slide(blank_layout)
    add_split_slide(
        s13,
        "Step 8: Controlled Execution Engine & Step Streaming",
        "STEP 8 — TEST EXECUTION & LIVE ASSERTIONS",
        "High-Speed Runner with Real-Time Assertion Streaming",
        [
            "Dual Execution Modes: Interactive headed browser for local visual debugging, or headless runner for CI/CD pipelines.",
            "Live Step Streaming: Displays every action, locator click, text input, and assertion check with millisecond timestamps.",
            "Browser Session Auto-Recovery: Closed browser targets automatically re-instantiate without aborting remaining test cases.",
            "Stop-on-Failure Protection: Automatically pauses or stops execution upon unexpected application crashes."
        ],
        "Native Playwright event dispatching ensures zero timing flakiness and exact assertion reproducibility.",
        "08_controlled_execution.png",
        "Live step execution log, pass/fail indicators, and execution controls"
    )

    # Slide 14: Step 9 Audit Reports
    s14 = prs.slides.add_slide(blank_layout)
    add_split_slide(
        s14,
        "Step 9: Audit-Ready Test Artifacts & 10-Sheet Workbook",
        "STEP 9 — AUDIT DOSSIERS & COMPLIANCE REPORTING",
        "Multi-Format Audit Dossiers & Step-by-Step Screenshot Evidence",
        [
            "10-Sheet Excel Workbook: Complete technical audit artifact detailing execution summary, route inventory, and security findings.",
            "CI/CD Integration: Generates standard JUnit XML and JSON summaries compatible with GitHub Actions, GitLab, and Jenkins.",
            "Executive HTML Dossier: Standalone interactive report shareable with compliance officers and VP Engineering.",
            "Forensic Screenshot Capture: Full-page screen captures recorded for every executed assertion and failure point."
        ],
        "Audit-ready compliance artifacts suitable for SOC2, ISO 27001, and HIPAA enterprise regulatory reviews.",
        "09_audit_reports.png",
        "Executive report summary cards, artifact download links, and metrics"
    )

    # Slide 15: Auxiliary Saved Runs
    s15 = prs.slides.add_slide(blank_layout)
    add_split_slide(
        s15,
        "Auxiliary Dashboard: Saved Runs & Checkpoint Recovery",
        "ENTERPRISE PERSISTENCE & FAULT TOLERANCE",
        "Transactional State Persistence on SQLite & PostgreSQL",
        [
            "Multi-Engine Relational Storage: Full persistence of all runs, routes, element catalogs, test suites, and audit logs.",
            "One-Click Checkpoint Resume: Resume an interrupted 2-hour scan from its exact recorded state without re-running.",
            "Typed Confirmation Guard: Destructive operations require typing 'DELETE' to prevent accidental test artifact loss.",
            "Multi-Process Database Locking: Safe transactional concurrency prevents run context corruption."
        ],
        "Complete historical scan and test execution audit trail retained indefinitely in enterprise database.",
        "10_saved_runs_dashboard.png",
        "Saved runs table, resume actions, and typed delete confirmation modal"
    )

    # Slide 16: Auxiliary Regression & Baselines
    s16 = prs.slides.add_slide(blank_layout)
    add_split_slide(
        s16,
        "Auxiliary Dashboard: Regression Suites & Visual Baselines",
        "CONTINUOUS TESTING & VISUAL DRIFT DETECTION",
        "Multi-Suite Bundling & Perceptual Diff Baselines",
        [
            "Suite Bundling: Group multiple functional test suites into scheduled regression sets (e.g. Daily Smoke, P0 Critical Path).",
            "Visual Perceptual Diffing: Detects subtle CSS shifts, font drops, and visual layout breakage against golden baselines.",
            "Historical Drift Tracking: Compares test timing, memory footprint, and pass rates across sequential software builds.",
            "Baseline Management: Promote approved UI changes to the new golden baseline with single-click operator approval."
        ],
        "Zero undetected visual or functional UI regressions reaching customer-facing production environments.",
        "11_regression_suites.png",
        "Regression suite manager, visual baseline comparison, and diff viewer"
    )

    # -------------------------------------------------------------
    # SLIDE 17: Enterprise Data Architecture & Relational Schema
    # -------------------------------------------------------------
    s17 = prs.slides.add_slide(blank_layout)
    add_table_slide(
        s17,
        "Enterprise Data Architecture & Relational Schema",
        "PERSISTENCE LAYER SPECIFICATION (SQLALCHEMY ORM)",
        ["Entity Name", "Key Attributes", "Relational Mapping", "Enterprise Purpose"],
        [
            ["RunContext", "run_id, target_url, status, checkpoint, created_at", "1:N Routes, 1:N Suites", "Top-level scan lifecycle & checkpoint pointer"],
            ["DiscoveredRoute", "route_id, run_id, path, http_status, classification", "N:1 RunContext, 1:N Elements", "Inventory of all crawled application pages"],
            ["ElementRegistry", "element_id, route_id, role, selector_score, bbox", "N:1 DiscoveredRoute", "12-attribute self-healing element registry"],
            ["RegressionSuite", "suite_id, name, run_id, test_case_count, status", "1:N TestCases, 1:1 Baseline", "Named bundle of test scenarios for CI/CD runs"],
            ["SecurityFinding", "finding_id, run_id, owasp_category, severity, url", "N:1 RunContext", "Passive DAST vulnerability audit log"],
            ["ArtifactReference", "artifact_id, run_id, file_type, file_path, size_bytes", "N:1 RunContext", "Tracked Excel workbooks, JUnit XML & reports"]
        ],
        [Inches(2.0), Inches(4.5), Inches(2.2), Inches(3.0)],
        "Supports SQLite for zero-config single-node setups and PostgreSQL for multi-user high-concurrency enterprise deployments."
    )

    # -------------------------------------------------------------
    # SLIDE 18: Security, Governance & Transport Guardrails
    # -------------------------------------------------------------
    s18 = prs.slides.add_slide(blank_layout)
    add_table_slide(
        s18,
        "Enterprise Security, Governance & Transport Guardrails",
        "SECURE ARCHITECTURE & RISK MITIGATION",
        ["Security Domain", "Threat Vector", "PiOne Testmate Guardrail", "Compliance Mapping"],
        [
            ["Transport SSRF", "Malicious redirects to cloud metadata (169.254.169.254) or VPC", "Playwright route interception mathematically drops non-target and private IP ranges", "CWE-918, OWASP A10"],
            ["Destructive Actions", "Accidental deletion of database records or billing transactions", "Safety Parking-Lot quarantines verbs (delete, cancel, buy) for human approval", "SOC2 CC7.2"],
            ["Credential Leakage", "Passwords or session tokens leaking in logs, reports, or CI artifacts", "Encrypted storage under reports/auth_sessions/; regex redaction on all logs", "ISO 27001 A.9.4"],
            ["AI Hallucinations", "LLM generating non-existent test steps causing pipeline failures", "Prompts strictly bounded to elements in the registered DOM catalog", "OWASP LLM09"],
            ["Session Hijacking", "Expired or stale session states invalidating long-running tests", "In-place /session/refresh preserves run context without re-authenticating", "OWASP A07"]
        ],
        [Inches(2.0), Inches(3.8), Inches(4.0), Inches(1.9)],
        "All guardrails are strictly enforced at the Python/Playwright transport layer and cannot be bypassed by AI models."
    )

    # -------------------------------------------------------------
    # SLIDE 19: Competitive Differentiation Matrix
    # -------------------------------------------------------------
    s19 = prs.slides.add_slide(blank_layout)
    add_table_slide(
        s19,
        "Competitive Differentiation Matrix",
        "MARKET COMPARISON & ENTERPRISE ADVANTAGE",
        ["Capability", "PiOne Testmate", "Selenium / WebDriver", "Cypress / Playwright", "Black-Box AI Bots"],
        [
            ["Setup Effort", "Zero-Script Autonomous", "High (Manual Coding)", "Medium (Manual Coding)", "Zero-Script"],
            ["Selector Maintenance", "Self-Healing (12 Attr)", "High Maintenance (40% time)", "High Maintenance", "Brittle / Hallucinates"],
            ["Destructive Protection", "Safety Parking Lot Enclave", "None (Blind Execution)", "None (Blind Execution)", "High Risk of Accidental Delete"],
            ["Security Auditing", "Built-in Passive DAST", "Requires 3rd-Party Tools", "Requires 3rd-Party Tools", "None"],
            ["AI Model Portability", "Gemini, OpenAI, Claude, Ollama", "None", "None", "Locked to Single Vendor"],
            ["State Persistence", "Relational Checkpoint Resume", "None (Restart from zero)", "None (Restart from zero)", "None"],
            ["Test Code Portability", "Emits Pure Pytest Code", "Custom Java/Python", "Custom JS/TS", "Proprietary Platform Lock-in"]
        ],
        [Inches(2.4), Inches(2.5), Inches(2.2), Inches(2.2), Inches(2.4)],
        "PiOne Testmate delivers the speed of autonomous AI with the deterministic safety and portability of pure Playwright code."
    )

    # -------------------------------------------------------------
    # SLIDE 20: Economic ROI, Deployment Options & Action Plan
    # -------------------------------------------------------------
    s20 = prs.slides.add_slide(blank_layout)
    set_slide_bg(s20, C_BG_LIGHT)
    add_header(s20, "Economic ROI, Deployment Options & Action Plan", "EXECUTIVE BUSINESS CASE & NEXT STEPS")

    # 3 ROI Cards
    roi_cards = [
        ("📈 85% Less Maintenance", "Reclaim hundreds of engineering hours lost to brittle manual selector updates. Autonomous route discovery adapts dynamically as your UI evolves.", C_EMERALD),
        ("⚡ 10x Test Velocity", "Synthesize complete, production-grade Playwright suites in minutes rather than weeks. Cut QA release cycles from 3 days to under 30 minutes.", C_BLUE),
        ("🔒 Air-Gapped Deployment", "Deploy in private cloud VPC, Kubernetes, or fully air-gapped on-premise with local Ollama inference. Zero test data or credentials leave your network.", C_PURPLE)
    ]
    for i, (r_title, r_desc, r_col) in enumerate(roi_cards):
        c_left = Inches(0.8 + i * 3.98)
        card = add_card(s20, c_left, Inches(1.5), Inches(3.76), Inches(2.5), C_WHITE, C_BORDER)
        tb = s20.shapes.add_textbox(c_left + Inches(0.2), Inches(1.7), Inches(3.36), Inches(2.1))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.paragraphs[0].text = r_title
        tf.paragraphs[0].font.size = Pt(16)
        tf.paragraphs[0].font.bold = True
        tf.paragraphs[0].font.color.rgb = r_col
        p = tf.add_paragraph()
        p.text = r_desc
        p.font.size = Pt(11)
        p.font.color.rgb = C_SLATE
        p.space_before = Pt(8)

    # Actionable 3-Phase Pilot Plan
    plan_card = add_card(s20, Inches(0.8), Inches(4.3), Inches(11.7), Inches(2.6), C_NAVY, C_NAVY)
    tb_plan = s20.shapes.add_textbox(Inches(1.0), Inches(4.45), Inches(11.3), Inches(2.3))
    tf_plan = tb_plan.text_frame
    tf_plan.word_wrap = True

    tf_plan.paragraphs[0].text = "ACTIONABLE ENTERPRISE PILOT ROADMAP"
    tf_plan.paragraphs[0].font.size = Pt(13)
    tf_plan.paragraphs[0].font.bold = True
    tf_plan.paragraphs[0].font.color.rgb = C_CYAN

    phases = [
        ("Phase 1: Day 1 Integration", "Deploy PiOne Testmate container in your staging VPC; configure target URLs and verify browser runtime readiness."),
        ("Phase 2: Week 1 Pilot Evaluation", "Scan 3 representative applications; catalog routes, run passive DAST scans, and generate grounded pytest suites."),
        ("Phase 3: Production CI/CD Rollout", "Integrate generated JUnit XML and HTML reports into your GitHub Actions / GitLab pipeline for automated gatekeeping.")
    ]
    for ph_title, ph_desc in phases:
        p = tf_plan.add_paragraph()
        p.text = f"▶ {ph_title}: {ph_desc}"
        p.font.size = Pt(10.5)
        p.font.color.rgb = RGBColor(226, 232, 240)
        p.space_before = Pt(6)

    # Save to all destination paths
    for p in deck_paths:
        try:
            prs.save(str(p))
            print(f"[SUCCESS] Master Presentation saved at: {p}")
        except Exception as e:
            print(f"[ERROR] Failed to save at {p}: {e}")

if __name__ == "__main__":
    build_master_deck()
