# Dev-time fixture only: renders a short spoken note to data/voice/sample_note.wav with Windows' local
# speech engine, so the ASR path can be tested without a person talking. Nothing here runs in the app,
# and it is not part of the runtime model stack. Replace the file with a real recording for the demo.
param([string]$Out = "data/voice/sample_note.wav")
Add-Type -AssemblyName System.Speech
$full = Join-Path (Get-Location) $Out
New-Item -ItemType Directory -Force -Path (Split-Path $full) | Out-Null
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(24000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile($full, $fmt)
$s.Rate = 0
$s.Speak("I'm onsite at the Acme Dental closet. The S F P on S W A C C zero two in rack A, U twelve, has been reseated, and the switch is power cycled.")
$s.Dispose()
Write-Output "wrote $full"
