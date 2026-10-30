# One film through the analysis pipeline. Two stages so the GPU runs one heavy model at a time.
#   .\run_film.ps1 -Id <youtube_id> -Stage cpu   # download, proxy, audio, shots + motion, contact sheets (no GPU models)
#   .\run_film.ps1 -Id <youtube_id> -Stage gpu   # whisper, demucs, audio report, voices, local-model tags (Ollama must be free)
param([Parameter(Mandatory)][string]$Id, [ValidateSet('cpu','gpu')][string]$Stage = 'cpu')
$ErrorActionPreference = 'Continue'  # 'Stop' + 2>&1 turns native stderr chatter into fatal errors in PS 5.1
if ($env:DOCUMENTARY_ROOT) { $root = $env:DOCUMENTARY_ROOT } else { $root = "$HOME\documentary" }
$work = "$root\work\$Id"
$ff   = (Resolve-Path "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Gyan.FFmpeg_*\ffmpeg-*\bin").Path
$vs   = "$env:APPDATA\VoiceStudio\runtime\project\.venv\Scripts\python.exe"   # has demucs + speechbrain
New-Item -ItemType Directory -Force "$work\frames" | Out-Null

function Step($name, [scriptblock]$b) { $t = Get-Date; Write-Output "== $name"; & $b; Write-Output ("   {0:N0} s" -f ((Get-Date) - $t).TotalSeconds) }

if ($Stage -eq 'cpu') {
  $mkv = "$root\video\$Id.mkv"
  if (-not (Test-Path $mkv)) { Step 'download' { py -3.13 "$root\dl.py" "https://youtu.be/$Id" "$root\video" 2>&1 | Select-String -NotMatch '\[download\]\s+\d' | Select-Object -Last 3 } }
  Step 'audio + proxy' {
    & "$ff\ffmpeg.exe" -v error -y -i $mkv -ac 1 -ar 16000 "$work\audio16k.wav"
    & "$ff\ffmpeg.exe" -v error -y -i $mkv -ac 2 -ar 44100 "$work\audio44k.wav"
    & "$ff\ffmpeg.exe" -v error -y -i $mkv -vf scale=960:-2 -c:v h264_nvenc -preset p4 -cq 20 -g 25 -an "$work\proxy.mp4"
  }
  Step 'shots + motion' { py -3.13 "$root\shots.py" $work 2>&1 | Select-String 'cuts:|frames:|DONE|Error' }
  Step 'contact sheets' { py -3.13 "$root\sheets.py" $work sheets }
} else {
  $env:HF_HUB_DISABLE_XET = '1'
  ollama stop huihui_ai/qwen3-vl-abliterated:8b-instruct 2>$null  # free VRAM for whisper/demucs; tag.py reloads it at the end
  Step 'whisper' { py -3.13 "$root\transcribe.py" $work 2>&1 | Select-String 'DONE|failed' }
  Step 'demucs' { $env:PATH = "$ff;$env:PATH"; & $vs -I -m demucs --two-stems vocals -n htdemucs -d cuda -o "$work\stems" "$work\audio44k.wav" 2>&1 | Select-String 'rror' }
  Step 'audio report' { & $vs -I "$root\audio.py" $work 2>&1 | Select-String '"wpm_overall"|voice_over_music|sound_hit' }
  Step 'voices' { & $vs -I "$root\voices.py" $work 2>&1 | Select-String "^\{'voice'" | Select-Object -First 8 }
  Step 'tags (local model)' { py -3.13 "$root\tag.py" $work 2>&1 | Select-String 'DONE|FAILED' }
}
Write-Output "STAGE $Stage DONE for $Id"
