import json
import os
import subprocess
import wave

SCENES = [
    {
        "id": 1,
        "title": "Problem: The QA Dilemma",
        "text": "Modern QA teams lose valuable sprint cycles manually mapping complex screens and constantly maintaining fragile UI test scripts."
    },
    {
        "id": 2,
        "title": "Discovery: Autonomous Scanning",
        "text": "PiOne Testmate scans authorised web applications, discovering navigation routes and interactive UI controls while preserving comprehensive evidence."
    },
    {
        "id": 3,
        "title": "Intelligence: Grounded AI Testing",
        "text": "With evidence-grounded test planning, generated test cases link directly to verified DOM elements and selectors, preventing hallucinated locators."
    },
    {
        "id": 4,
        "title": "Governance: Interactive Learning",
        "text": "Through interactive checkpoints, testers can guide discovery, teach key workflows, and safely quarantine risky actions before deep crawling."
    },
    {
        "id": 5,
        "title": "Validation: Execution and Reporting",
        "text": "Execute tests with live assertion streaming and per-step screenshots. Export ten-sheet Excel workbooks, save checkpoints, and compare regression baselines."
    },
    {
        "id": 6,
        "title": "Closing: The Evidence-First Platform",
        "text": "PiOne Testmate - evidence-first AI QA for controlled, explainable test automation."
    }
]

OUT_DIR = os.path.abspath("presentation/demo_video/audio")
os.makedirs(OUT_DIR, exist_ok=True)

manifest = []

for sc in SCENES:
    wav_path = os.path.join(OUT_DIR, f"scene{sc['id']}.wav").replace("\\", "/")
    escaped_text = sc['text'].replace("'", "''")
    ps_cmd = f"""
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {{ $synth.SelectVoice('Microsoft Zira Desktop') }} catch {{ }}
$synth.Rate = 0
$synth.SetOutputToWaveFile('{wav_path}')
$synth.Speak('{escaped_text}')
$synth.Dispose()
"""
    # Run powershell
    res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Error synthesizing scene {sc['id']}: {res.stderr}")
    else:
        # Get wave duration
        with wave.open(wav_path, 'rb') as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            duration = frames / float(rate)
        
        manifest.append({
            "id": sc['id'],
            "title": sc['title'],
            "text": sc['text'],
            "audio_file": f"scene{sc['id']}.wav",
            "duration": round(duration, 2)
        })
        print(f"Scene {sc['id']} ({duration:.2f}s): {sc['text'][:60]}...")

total_dur = sum(m['duration'] for m in manifest)
print(f"\nTotal Narration Duration: {total_dur:.2f} seconds")

manifest_path = os.path.join(OUT_DIR, "timing_manifest.json")
with open(manifest_path, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)

print(f"Saved manifest to {manifest_path}")
