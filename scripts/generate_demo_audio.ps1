Add-Type -AssemblyName System.Speech

$scenes = @(
    @{
        Id = 1
        Text = "Modern QA teams lose valuable sprint cycles manually mapping complex application screens and constantly fixing fragile, repetitive UI test scripts."
    },
    @{
        Id = 2
        Text = "PiOne Testmate helps teams scan authorised web applications, systematically discovering navigation routes and interactive UI elements while capturing comprehensive evidence at every step."
    },
    @{
        Id = 3
        Text = "Through evidence-grounded test planning, generated test cases link directly to verified DOM elements and robust CSS selectors, preventing hallucinated locators and brittle assertions."
    },
    @{
        Id = 4
        Text = "With interactive learning checkpoints, testers can guide discovery, teach high-priority workflows, and safely quarantine risky actions before deep crawling begins."
    },
    @{
        Id = 5
        Text = "Execute tests with live assertion streaming and per-step screenshots. Export enterprise ten-sheet Excel workbooks, save durable checkpoints, and compare visual regression baselines across releases."
    },
    @{
        Id = 6
        Text = "PiOne Testmate — evidence-first AI QA for controlled, explainable test automation."
    }
)

$outDir = "presentation/demo_video/audio"
if (-not (Test-Path $outDir)) {
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
}

$results = @()

foreach ($scene in $scenes) {
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    # Try Zira (female, natural) or David (male)
    try {
        $synth.SelectVoice("Microsoft Zira Desktop")
    } catch {
        # Fallback to default voice
    }
    $synth.Rate = 0  # Standard natural pace

    $wavPath = Join-Path $outDir ("scene" + $scene.Id + ".wav")
    $synth.SetOutputToWaveFile($wavPath)
    $synth.Speak($scene.Text)
    $synth.Dispose()

    # Measure audio duration using System.Media or wave header
    $fileBytes = [System.IO.File]::ReadAllBytes($wavPath)
    # WAV header byte rate is at offset 28 (4 bytes)
    # Data size is at offset 40 (4 bytes)
    $byteRate = [System.BitConverter]::ToInt32($fileBytes, 28)
    $dataSize = [System.BitConverter]::ToInt32($fileBytes, 40)
    $durationSec = [math]::Round($dataSize / $byteRate, 2)

    $results += [PSCustomObject]@{
        Scene = $scene.Id
        DurationSec = $durationSec
        Path = $wavPath
        Text = $scene.Text
    }
    Write-Host ("Scene " + $scene.Id + ": " + $durationSec + "s - " + $wavPath)
}

$totalDuration = ($results | Measure-Object -Property DurationSec -Sum).Sum
Write-Host ("`nTotal Audio Duration: " + $totalDuration + " seconds")

$results | ConvertTo-Json | Set-Content (Join-Path $outDir "timing_manifest.json")
