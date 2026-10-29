$ErrorActionPreference = "Stop"

Write-Host "=========================================================="
Write-Host "BUILDING PIONE TESTMATE V2 ENTERPRISE DEMO VIDEO AND SLIDES"
Write-Host "=========================================================="

$root = (Resolve-Path ".").Path
$outDir = Join-Path $root "presentation\demo_video"
$audioDir = Join-Path $outDir "audio"
$imgDir = Join-Path $root "presentation\images"
$v25Dir = Join-Path $imgDir "v2.5"

$pptxPath = Join-Path $outDir "pione_testmate_demo_slides.pptx"
$mp4Path = Join-Path $outDir "pione_testmate_v2_demo.mp4"

if (Test-Path $mp4Path) { Remove-Item $mp4Path -Force }

$ppt = New-Object -ComObject PowerPoint.Application
$ppt.WindowState = 2 # ppWindowMinimized

$pres = $ppt.Presentations.Add([Microsoft.Office.Core.MsoTriState]::msoTrue)
$pres.PageSetup.SlideWidth = 960  # 16:9 widescreen
$pres.PageSetup.SlideHeight = 540

# Colors (BGR hex format for PowerPoint)
$colBg = 0x190F0B       # #0B0F19 Dark Navy
$colCard = 0x2A1C14     # #141C2A Slate Blue Card
$colCyan = 0xF8BD38     # #38BDF8 Electric Cyan
$colEmerald = 0x81B910  # #10B981 Emerald
$colWhite = 0xFFFFFF    # White
$colMuted = 0xCBD5E1    # Muted Gray

function Add-Header($slide, $badgeText, $titleText, $subText) {
    # Badge
    $b = $slide.Shapes.AddTextbox(1, 50, 30, 450, 24)
    $b.TextFrame.TextRange.Text = $badgeText.ToUpper()
    $b.TextFrame.TextRange.Font.Name = "Segoe UI"
    $b.TextFrame.TextRange.Font.Size = 10
    $b.TextFrame.TextRange.Font.Bold = [Microsoft.Office.Core.MsoTriState]::msoTrue
    $b.TextFrame.TextRange.Font.Color.RGB = $colCyan
    $b.TextFrame.MarginLeft = 0
    $b.TextFrame.MarginTop = 0

    # Title
    $t = $slide.Shapes.AddTextbox(1, 50, 50, 500, 40)
    $t.TextFrame.TextRange.Text = $titleText
    $t.TextFrame.TextRange.Font.Name = "Segoe UI"
    $t.TextFrame.TextRange.Font.Size = 22
    $t.TextFrame.TextRange.Font.Bold = [Microsoft.Office.Core.MsoTriState]::msoTrue
    $t.TextFrame.TextRange.Font.Color.RGB = $colWhite
    $t.TextFrame.MarginLeft = 0
    $t.TextFrame.MarginTop = 0

    # Subtitle
    $s = $slide.Shapes.AddTextbox(1, 50, 92, 550, 26)
    $s.TextFrame.TextRange.Text = $subText
    $s.TextFrame.TextRange.Font.Name = "Segoe UI"
    $s.TextFrame.TextRange.Font.Size = 11
    $s.TextFrame.TextRange.Font.Color.RGB = $colMuted
    $s.TextFrame.MarginLeft = 0
    $s.TextFrame.MarginTop = 0
}

function Add-Audio($slide, $wavFile) {
    $audioShape = $slide.Shapes.AddMediaObject2($wavFile, [Microsoft.Office.Core.MsoTriState]::msoFalse, [Microsoft.Office.Core.MsoTriState]::msoTrue, 920, 10, 24, 24)
    $audioShape.AnimationSettings.PlaySettings.PlayOnEntry = [Microsoft.Office.Core.MsoTriState]::msoTrue
    $audioShape.AnimationSettings.PlaySettings.HideWhileNotPlaying = [Microsoft.Office.Core.MsoTriState]::msoTrue
}

function Add-PipPresenter($slide) {
    $pipPath = Join-Path $outDir "synthetic_presenter.jpg"
    $pic = $slide.Shapes.AddPicture($pipPath, [Microsoft.Office.Core.MsoTriState]::msoFalse, [Microsoft.Office.Core.MsoTriState]::msoTrue, 50, 395, 110, 110)
    $pic.Line.ForeColor.RGB = $colCyan
    $pic.Line.Weight = 2

    $box = $slide.Shapes.AddTextbox(1, 170, 430, 250, 40)
    $box.TextFrame.TextRange.Text = "AI Synthetic Presenter`nPiOne Technical Briefing"
    $box.TextFrame.TextRange.Font.Name = "Segoe UI"
    $box.TextFrame.TextRange.Font.Size = 9
    $box.TextFrame.TextRange.Font.Color.RGB = $colMuted
}

# -------------------------------------------------------------
# SLIDE 1: The QA Dilemma (Duration: 9s)
# -------------------------------------------------------------
Write-Host "Creating Slide 1: The QA Dilemma..."
$s1 = $pres.Slides.Add(1, 12)
$s1.Background.Fill.Solid()
$s1.Background.Fill.ForeColor.RGB = $colBg
$s1.SlideShowTransition.AdvanceOnTime = [Microsoft.Office.Core.MsoTriState]::msoTrue
$s1.SlideShowTransition.AdvanceTime = 9.0

# Presenter Hero Left
$presHero = Join-Path $outDir "synthetic_presenter.jpg"
$pic1 = $s1.Shapes.AddPicture($presHero, [Microsoft.Office.Core.MsoTriState]::msoFalse, [Microsoft.Office.Core.MsoTriState]::msoTrue, 50, 40, 380, 460)
$pic1.Line.ForeColor.RGB = $colCyan
$pic1.Line.Weight = 2

# Right Card
$card1 = $s1.Shapes.AddShape(1, 460, 40, 450, 460)
$card1.Fill.Solid()
$card1.Fill.ForeColor.RGB = $colCard
$card1.Line.ForeColor.RGB = 0x3E291C
$card1.Line.Weight = 1

$b1 = $s1.Shapes.AddTextbox(1, 485, 60, 400, 24)
$b1.TextFrame.TextRange.Text = "ENTERPRISE QA CHALLENGE"
$b1.TextFrame.TextRange.Font.Name = "Segoe UI"
$b1.TextFrame.TextRange.Font.Size = 11
$b1.TextFrame.TextRange.Font.Bold = [Microsoft.Office.Core.MsoTriState]::msoTrue
$b1.TextFrame.TextRange.Font.Color.RGB = $colCyan

$t1 = $s1.Shapes.AddTextbox(1, 485, 88, 400, 75)
$t1.TextFrame.TextRange.Text = "The Enterprise Web QA Dilemma"
$t1.TextFrame.TextRange.Font.Name = "Segoe UI"
$t1.TextFrame.TextRange.Font.Size = 26
$t1.TextFrame.TextRange.Font.Bold = [Microsoft.Office.Core.MsoTriState]::msoTrue
$t1.TextFrame.TextRange.Font.Color.RGB = $colWhite

# Problem Cards
$pBox1 = $s1.Shapes.AddShape(1, 485, 175, 400, 95)
$pBox1.Fill.Solid()
$pBox1.Fill.ForeColor.RGB = 0x221711
$pBox1.Line.ForeColor.RGB = 0x4B2C24
$pText1 = $s1.Shapes.AddTextbox(1, 500, 185, 370, 75)
$pText1.TextFrame.TextRange.Text = "Manual Screen Mapping Wastes Time`nTeams spend 40% to 50% of test sprint cycles manually mapping routes and hand-authoring repetitive locators."
$pText1.TextFrame.TextRange.Font.Name = "Segoe UI"
$pText1.TextFrame.TextRange.Font.Size = 11
$pText1.TextFrame.TextRange.Font.Color.RGB = $colMuted
$pText1.TextFrame.TextRange.Lines(1).Font.Bold = [Microsoft.Office.Core.MsoTriState]::msoTrue
$pText1.TextFrame.TextRange.Lines(1).Font.Color.RGB = $colWhite

$pBox2 = $s1.Shapes.AddShape(1, 485, 285, 400, 95)
$pBox2.Fill.Solid()
$pBox2.Fill.ForeColor.RGB = 0x221711
$pBox2.Line.ForeColor.RGB = 0x4B2C24
$pText2 = $s1.Shapes.AddTextbox(1, 500, 295, 370, 75)
$pText2.TextFrame.TextRange.Text = "Fragile Locators and Hallucinating Bots`nMinor frontend changes break brittle CSS selectors, while unconstrained black-box AI tools invent non-existent UI elements."
$pText2.TextFrame.TextRange.Font.Name = "Segoe UI"
$pText2.TextFrame.TextRange.Font.Size = 11
$pText2.TextFrame.TextRange.Font.Color.RGB = $colMuted
$pText2.TextFrame.TextRange.Lines(1).Font.Bold = [Microsoft.Office.Core.MsoTriState]::msoTrue
$pText2.TextFrame.TextRange.Lines(1).Font.Color.RGB = $colWhite

$solBox = $s1.Shapes.AddTextbox(1, 485, 400, 400, 60)
$solBox.TextFrame.TextRange.Text = "PiOne Testmate bridges deterministic browser scanning with evidence-grounded AI intelligence."
$solBox.TextFrame.TextRange.Font.Name = "Segoe UI"
$solBox.TextFrame.TextRange.Font.Size = 12
$solBox.TextFrame.TextRange.Font.Bold = [Microsoft.Office.Core.MsoTriState]::msoTrue
$solBox.TextFrame.TextRange.Font.Color.RGB = $colEmerald

Add-Audio $s1 (Join-Path $audioDir "scene1.wav")


# -------------------------------------------------------------
# SLIDE 2: Autonomous Discovery (Duration: 10s)
# -------------------------------------------------------------
Write-Host "Creating Slide 2: Autonomous Discovery..."
$s2 = $pres.Slides.Add(2, 12)
$s2.Background.Fill.Solid()
$s2.Background.Fill.ForeColor.RGB = $colBg
$s2.SlideShowTransition.AdvanceOnTime = [Microsoft.Office.Core.MsoTriState]::msoTrue
$s2.SlideShowTransition.AdvanceTime = 10.0

Add-Header $s2 "AUTONOMOUS DISCOVERY AND EVIDENCE CAPTURE" "Scan Authorised Apps and Preserve Evidence" "Playwright-Powered Bounded Traversal -- Strict SSRF Protection"

$c2 = $s2.Shapes.AddShape(1, 50, 135, 370, 245)
$c2.Fill.Solid()
$c2.Fill.ForeColor.RGB = $colCard
$c2.Line.ForeColor.RGB = 0x3E291C
$c2.Line.Weight = 1

$c2t = $s2.Shapes.AddTextbox(1, 65, 145, 340, 220)
$c2t.TextFrame.TextRange.Text = "Deterministic Exploration First:`n`n- Discovers sidebars, top menus, and navigation trees`n- 12-attribute DOM fingerprinting for every target`n- Scope-locked to target origin with SSRF protection`n- Preserves atomic RunContext and route graphs"
$c2t.TextFrame.TextRange.Font.Name = "Segoe UI"
$c2t.TextFrame.TextRange.Font.Size = 11
$c2t.TextFrame.TextRange.Font.Color.RGB = $colMuted
$c2t.TextFrame.TextRange.Lines(1).Font.Bold = [Microsoft.Office.Core.MsoTriState]::msoTrue
$c2t.TextFrame.TextRange.Lines(1).Font.Color.RGB = $colWhite

Add-PipPresenter $s2

$shot2a = Join-Path $v25Dir "01_step1_initial_landing.png"
$pic2a = $s2.Shapes.AddPicture($shot2a, [Microsoft.Office.Core.MsoTriState]::msoFalse, [Microsoft.Office.Core.MsoTriState]::msoTrue, 445, 135, 465, 175)
$pic2a.Line.ForeColor.RGB = $colCyan
$pic2a.Line.Weight = 1.5

$shot2b = Join-Path $v25Dir "04_step2_modules_discovered.png"
$pic2b = $s2.Shapes.AddPicture($shot2b, [Microsoft.Office.Core.MsoTriState]::msoFalse, [Microsoft.Office.Core.MsoTriState]::msoTrue, 445, 325, 465, 175)
$pic2b.Line.ForeColor.RGB = $colCyan
$pic2b.Line.Weight = 1.5

Add-Audio $s2 (Join-Path $audioDir "scene2.wav")


# -------------------------------------------------------------
# SLIDE 3: Grounded AI Test Planning (Duration: 10s)
# -------------------------------------------------------------
Write-Host "Creating Slide 3: Grounded AI Test Planning..."
$s3 = $pres.Slides.Add(3, 12)
$s3.Background.Fill.Solid()
$s3.Background.Fill.ForeColor.RGB = $colBg
$s3.SlideShowTransition.AdvanceOnTime = [Microsoft.Office.Core.MsoTriState]::msoTrue
$s3.SlideShowTransition.AdvanceTime = 10.0

Add-Header $s3 "GROUNDED AI TEST PLANNING" "100% DOM-Anchored Selectors" "Zero Hallucinations -- Multi-Provider Neutral LLM Gateway"

$c3 = $s3.Shapes.AddShape(1, 50, 135, 370, 245)
$c3.Fill.Solid()
$c3.Fill.ForeColor.RGB = $colCard
$c3.Line.ForeColor.RGB = 0x3E291C
$c3.Line.Weight = 1

$c3t = $s3.Shapes.AddTextbox(1, 65, 145, 340, 220)
$c3t.TextFrame.TextRange.Text = "Zero Hallucination Guarantee:`n`n- Every test step is anchored to real DOM evidence`n- Role, CSS selector, and XPath fallback hierarchy`n- Shortfall Protection Guard: Explains evidence bounds if fewer safe controls exist instead of inventing tests`n- Supports Gemini, Claude, GPT-4o, and local Ollama"
$c3t.TextFrame.TextRange.Font.Name = "Segoe UI"
$c3t.TextFrame.TextRange.Font.Size = 11
$c3t.TextFrame.TextRange.Font.Color.RGB = $colMuted
$c3t.TextFrame.TextRange.Lines(1).Font.Bold = [Microsoft.Office.Core.MsoTriState]::msoTrue
$c3t.TextFrame.TextRange.Lines(1).Font.Color.RGB = $colWhite

Add-PipPresenter $s3

$shot3 = Join-Path $v25Dir "15_step6_generate_tests_plan_table.png"
$pic3 = $s3.Shapes.AddPicture($shot3, [Microsoft.Office.Core.MsoTriState]::msoFalse, [Microsoft.Office.Core.MsoTriState]::msoTrue, 445, 135, 465, 340)
$pic3.Line.ForeColor.RGB = $colEmerald
$pic3.Line.Weight = 2

$badge3 = $s3.Shapes.AddShape(1, 520, 485, 320, 30)
$badge3.Fill.Solid()
$badge3.Fill.ForeColor.RGB = 0x221711
$badge3.Line.ForeColor.RGB = $colEmerald
$b3t = $s3.Shapes.AddTextbox(1, 520, 490, 320, 24)
$b3t.TextFrame.TextRange.Text = "[CONFIRMED] 100% GROUNDED IN REAL DOM CONTROLS"
$b3t.TextFrame.TextRange.Font.Name = "Segoe UI"
$b3t.TextFrame.TextRange.Font.Size = 9
$b3t.TextFrame.TextRange.Font.Bold = [Microsoft.Office.Core.MsoTriState]::msoTrue
$b3t.TextFrame.TextRange.Font.Color.RGB = $colEmerald
$b3t.TextFrame.TextRange.ParagraphFormat.Alignment = 2

Add-Audio $s3 (Join-Path $audioDir "scene3.wav")


# -------------------------------------------------------------
# SLIDE 4: Human-in-the-Loop Governance (Duration: 10s)
# -------------------------------------------------------------
Write-Host "Creating Slide 4: Interactive Governance..."
$s4 = $pres.Slides.Add(4, 12)
$s4.Background.Fill.Solid()
$s4.Background.Fill.ForeColor.RGB = $colBg
$s4.SlideShowTransition.AdvanceOnTime = [Microsoft.Office.Core.MsoTriState]::msoTrue
$s4.SlideShowTransition.AdvanceTime = 10.0

Add-Header $s4 "HUMAN-IN-THE-LOOP GOVERNANCE" "Interactive Checkpoints and Safety Enclaves" "ActionClassifier Quarantines Risky Verbs -- Supervisory Veto Power"

$c4 = $s4.Shapes.AddShape(1, 50, 135, 370, 245)
$c4.Fill.Solid()
$c4.Fill.ForeColor.RGB = $colCard
$c4.Line.ForeColor.RGB = 0x3E291C
$c4.Line.Weight = 1

$c4t = $s4.Shapes.AddTextbox(1, 65, 145, 340, 220)
$c4t.TextFrame.TextRange.Text = "Safety and Supervised Control:`n`n- Safety Parking Lot quarantines destructive verbs (delete, pay, purge, cancel, submit)`n- Interactive browser overlays inject checkpoint banner`n- Testers select Confirm and Continue or End Scan`n- Real-time route graph records manual guidance"
$c4t.TextFrame.TextRange.Font.Name = "Segoe UI"
$c4t.TextFrame.TextRange.Font.Size = 11
$c4t.TextFrame.TextRange.Font.Color.RGB = $colMuted
$c4t.TextFrame.TextRange.Lines(1).Font.Bold = [Microsoft.Office.Core.MsoTriState]::msoTrue
$c4t.TextFrame.TextRange.Lines(1).Font.Color.RGB = $colWhite

Add-PipPresenter $s4

$shot4a = Join-Path $v25Dir "07_step3_safety_parking_lot.png"
$pic4a = $s4.Shapes.AddPicture($shot4a, [Microsoft.Office.Core.MsoTriState]::msoFalse, [Microsoft.Office.Core.MsoTriState]::msoTrue, 445, 135, 465, 175)
$pic4a.Line.ForeColor.RGB = $colCyan
$pic4a.Line.Weight = 1.5

$shot4b = Join-Path $v25Dir "24_step3_interactive_checkpoint_banner.png"
$pic4b = $s4.Shapes.AddPicture($shot4b, [Microsoft.Office.Core.MsoTriState]::msoFalse, [Microsoft.Office.Core.MsoTriState]::msoTrue, 445, 325, 465, 175)
$pic4b.Line.ForeColor.RGB = $colCyan
$pic4b.Line.Weight = 1.5

Add-Audio $s4 (Join-Path $audioDir "scene4.wav")


# -------------------------------------------------------------
# SLIDE 5: Controlled Execution & Audit Reporting (Duration: 11s)
# -------------------------------------------------------------
Write-Host "Creating Slide 5: Execution and Reporting..."
$s5 = $pres.Slides.Add(5, 12)
$s5.Background.Fill.Solid()
$s5.Background.Fill.ForeColor.RGB = $colBg
$s5.SlideShowTransition.AdvanceOnTime = [Microsoft.Office.Core.MsoTriState]::msoTrue
$s5.SlideShowTransition.AdvanceTime = 11.0

Add-Header $s5 "CONTROLLED EXECUTION AND AUDIT REPORTING" "Live Assertions and 10-Sheet Workbook" "Per-Step Forensic Screenshots -- Tamper-Evident Multi-Format Exports"

$c5 = $s5.Shapes.AddShape(1, 50, 135, 370, 245)
$c5.Fill.Solid()
$c5.Fill.ForeColor.RGB = $colCard
$c5.Line.ForeColor.RGB = 0x3E291C
$c5.Line.Weight = 1

$c5t = $s5.Shapes.AddTextbox(1, 65, 145, 340, 220)
$c5t.TextFrame.TextRange.Text = "Verified Execution and Compliance:`n`n- 82/82 Steps Passed in Rendered Execution (100% Pass)`n- Full-fidelity screenshot captured per assertion`n- 10-Sheet audit Excel workbook: working routes, element map, parking lot, execution log`n- Golden Baseline freezing for regression comparison"
$c5t.TextFrame.TextRange.Font.Name = "Segoe UI"
$c5t.TextFrame.TextRange.Font.Size = 11
$c5t.TextFrame.TextRange.Font.Color.RGB = $colMuted
$c5t.TextFrame.TextRange.Lines(1).Font.Bold = [Microsoft.Office.Core.MsoTriState]::msoTrue
$c5t.TextFrame.TextRange.Lines(1).Font.Color.RGB = $colWhite

Add-PipPresenter $s5

$shot5a = Join-Path $v25Dir "18_step7_execute_completed_pass.png"
$pic5a = $s5.Shapes.AddPicture($shot5a, [Microsoft.Office.Core.MsoTriState]::msoFalse, [Microsoft.Office.Core.MsoTriState]::msoTrue, 445, 135, 465, 175)
$pic5a.Line.ForeColor.RGB = $colEmerald
$pic5a.Line.Weight = 1.5

$shot5b = Join-Path $v25Dir "19_step8_report_dashboard.png"
$pic5b = $s5.Shapes.AddPicture($shot5b, [Microsoft.Office.Core.MsoTriState]::msoFalse, [Microsoft.Office.Core.MsoTriState]::msoTrue, 445, 325, 465, 175)
$pic5b.Line.ForeColor.RGB = $colCyan
$pic5b.Line.Weight = 1.5

Add-Audio $s5 (Join-Path $audioDir "scene5.wav")


# -------------------------------------------------------------
# SLIDE 6: Closing & Brand Promise (Duration: 7s)
# -------------------------------------------------------------
Write-Host "Creating Slide 6: Closing and Brand Promise..."
$s6 = $pres.Slides.Add(6, 12)
$s6.Background.Fill.Solid()
$s6.Background.Fill.ForeColor.RGB = $colBg
$s6.SlideShowTransition.AdvanceOnTime = [Microsoft.Office.Core.MsoTriState]::msoTrue
$s6.SlideShowTransition.AdvanceTime = 7.0

$presOutro = Join-Path $outDir "synthetic_presenter_outro.jpg"
$pic6 = $s6.Shapes.AddPicture($presOutro, [Microsoft.Office.Core.MsoTriState]::msoFalse, [Microsoft.Office.Core.MsoTriState]::msoTrue, 530, 40, 380, 460)
$pic6.Line.ForeColor.RGB = $colCyan
$pic6.Line.Weight = 2

$card6 = $s6.Shapes.AddShape(1, 50, 40, 450, 460)
$card6.Fill.Solid()
$card6.Fill.ForeColor.RGB = $colCard
$card6.Line.ForeColor.RGB = 0x3E291C
$card6.Line.Weight = 1

$b6 = $s6.Shapes.AddTextbox(1, 75, 65, 400, 24)
$b6.TextFrame.TextRange.Text = "THE EVIDENCE-FIRST QA PLATFORM"
$b6.TextFrame.TextRange.Font.Name = "Segoe UI"
$b6.TextFrame.TextRange.Font.Size = 11
$b6.TextFrame.TextRange.Font.Bold = [Microsoft.Office.Core.MsoTriState]::msoTrue
$b6.TextFrame.TextRange.Font.Color.RGB = $colCyan

$t6 = $s6.Shapes.AddTextbox(1, 75, 95, 400, 50)
$t6.TextFrame.TextRange.Text = "PiOne Testmate"
$t6.TextFrame.TextRange.Font.Name = "Segoe UI"
$t6.TextFrame.TextRange.Font.Size = 32
$t6.TextFrame.TextRange.Font.Bold = [Microsoft.Office.Core.MsoTriState]::msoTrue
$t6.TextFrame.TextRange.Font.Color.RGB = $colWhite

$sub6 = $s6.Shapes.AddTextbox(1, 75, 148, 400, 45)
$sub6.TextFrame.TextRange.Text = "Evidence-first AI QA for controlled, explainable test automation."
$sub6.TextFrame.TextRange.Font.Name = "Segoe UI"
$sub6.TextFrame.TextRange.Font.Size = 13
$sub6.TextFrame.TextRange.Font.Bold = [Microsoft.Office.Core.MsoTriState]::msoTrue
$sub6.TextFrame.TextRange.Font.Color.RGB = $colEmerald

$pills = $s6.Shapes.AddTextbox(1, 75, 205, 400, 190)
$pills.TextFrame.TextRange.Text = "Enterprise Assurance Capabilities:`n`n- Autonomous Discovery Without Manual Scripts`n- Grounded Test Planning Without Hallucinations`n- Human-in-the-Loop Checkpoints With Safety Enclaves`n- SSRF Protection and Parking Lot Safety`n- 10-Sheet Audit-Ready Compliance Workbooks`n- Transactional Persistence and Golden Baselines"
$pills.TextFrame.TextRange.Font.Name = "Segoe UI"
$pills.TextFrame.TextRange.Font.Size = 11
$pills.TextFrame.TextRange.Font.Color.RGB = $colMuted
$pills.TextFrame.TextRange.Lines(1).Font.Bold = [Microsoft.Office.Core.MsoTriState]::msoTrue
$pills.TextFrame.TextRange.Lines(1).Font.Color.RGB = $colWhite

$foot6 = $s6.Shapes.AddTextbox(1, 75, 420, 400, 50)
$foot6.TextFrame.TextRange.Text = "Explore the Documentation Hub and Live Demo`nPiOne Technologies LLP -- V2 Enterprise Baseline"
$foot6.TextFrame.TextRange.Font.Name = "Segoe UI"
$foot6.TextFrame.TextRange.Font.Size = 10
$foot6.TextFrame.TextRange.Font.Color.RGB = $colCyan

Add-Audio $s6 (Join-Path $audioDir "scene6.wav")


# -------------------------------------------------------------
# SAVE PPTX AND RENDER 1080P MP4
# -------------------------------------------------------------
Write-Host "`nSaving PowerPoint presentation package: $pptxPath"
$pres.SaveAs($pptxPath)

Write-Host "Triggering native 1080p MP4 video export: $mp4Path"
$pres.SaveAs($mp4Path, 39)

Write-Host "Waiting for video export to complete..."
$count = 0
while ($true) {
    Start-Sleep -Seconds 1
    $count++
    $st = $pres.CreateVideoStatus
    if (Test-Path $mp4Path) {
        $size = (Get-Item $mp4Path).Length
        Write-Host ("[" + $count + "s] Status=" + $st + ", Output Size=" + [math]::Round($size / 1KB, 1) + " KB")
        if ($size -gt 100000 -and $st -ne 1) {
            Write-Host "Video export completed successfully!"
            break
        }
    } else {
        Write-Host ("[" + $count + "s] Status=" + $st + ", preparing video file...")
    }
    if ($count -gt 120) {
        Write-Warning "Video export reached timeout (120s)."
        break
    }
}

$pres.Close()
$ppt.Quit()
[System.Runtime.Interopservices.Marshal]::ReleaseComObject($ppt) | Out-Null

if (Test-Path $mp4Path) {
    $finalSize = (Get-Item $mp4Path).Length
    Write-Host "`nSUCCESS! Generated MP4 Video:"
    Write-Host "  Path: $mp4Path"
    Write-Host "  Size: $([math]::Round($finalSize / 1MB, 2)) MB ($finalSize bytes)"
} else {
    Write-Error "MP4 video export failed to generate file."
}
