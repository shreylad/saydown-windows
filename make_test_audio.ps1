# Writes test recordings with the Windows text-to-speech voice: 16 kHz mono WAV files in .\test-audio.
# They let test_e2e.py run a whole dictation (shortcut, recording, Whisper, cleanup, paste) without a microphone.
Add-Type -AssemblyName System.Speech
$out = Join-Path $PSScriptRoot "test-audio"
New-Item -ItemType Directory -Force $out | Out-Null
$samples = [ordered]@{
    "plain"    = "Hello team, the launch moves to Thursday. Please review the report before the meeting."
    "filler"   = "Um, so I think, uh, the plan is fine."
    "list"     = "First, review the report. Second, send the invoice. Third, book the flights."
    "bullets"  = "Here are my tasks: writing code, testing it, and shipping it."
    "correction" = "Let's meet on Tuesday, no wait, Wednesday at three."
    "scratch"  = "Scratch that."
}
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
foreach ($name in $samples.Keys) {
    $voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $voice.Rate = 0
    $voice.SetOutputToWaveFile((Join-Path $out "$name.wav"), $format)
    $voice.Speak($samples[$name])
    $voice.Dispose()
    Write-Output "$name.wav: $($samples[$name])"
}
