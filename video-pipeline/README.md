# Video understanding pipeline: from a long video to notes and short clips

Send a YouTube video, an Instagram Reel or a TikTok link (by Telegram, or from a saved collection). The pipeline downloads it, splits it into **audio + frames + cuts on one clock**, measures everything with **local** models, and writes **compact files** that a large model (Claude) can read in seconds. The outputs are a study document of the video and, for long streams, ready-to-review vertical short clips.

```
 link (Telegram bot / saved collection / channel watch)
   │
   ▼
 yt-dlp  ──►  video.mkv/mp4 + info.json (+ subtitles, comments)
   │
   ├─ FFmpeg ─► audio 16 kHz mono ─► faster-whisper large-v3 ─► words with start/end times
   │                 └─ 44 kHz ─► Demucs ─► voice stem + music stem ─► pace, pauses, pitch, music ducking, sound hits on cuts
   │                                              └─ SpeechBrain ECAPA ─► who speaks when (voice clusters)
   ├─ FFmpeg ─► 960 px proxy ─► PySceneDetect ─► every cut (shot table)
   │                         └─ OpenCV optical flow, every frame ─► pan / tilt / zoom / shake / parallax, brightness, palette
   │                         └─ one mid-frame per shot ─► local Qwen3-VL 8B (Ollama) ─► medium, shot size, angle, text, effects (JSON)
   │                         └─ contact sheets: 48 shots per image
   ▼
 film_card.md (every number in one page) + transcript.txt + contact sheets
   │
   ▼
 Claude reads ONLY these ─► study notes / documentation
   │
   └─ long streams: moment finder ─► judge (Claude Haiku, local Qwen as fallback) ─► FFmpeg cutter ─► 1080x1920 clips with word-timed captions
                                   └─ writer (Claude Sonnet) ─► hooks, title, caption ─► Voice Check (rules + local model) ─► human review file
```

## Folders

| Folder | What runs there | Key files |
|---|---|---|
| `1-intake/` | Getting the video | `reel_bot.py`: private Telegram bot. Paired to one owner by a one-time code; accepts Instagram, YouTube and TikTok links or a video file under 20 MB. `collect_links.py`: reads saved Instagram collections. `loop.py`: every N minutes, collect → download → analyse → delete old local videos, with RAM/VRAM checks |
| `2-film-analysis/` | Deep analysis of one video | `run_film.ps1`: two stages so the GPU runs one heavy model at a time. `dl.py` (yt-dlp), `transcribe.py` (Whisper), `shots.py` (cuts + motion + palette), `sheets.py` (contact sheets), `audio.py` (Demucs stems → voice and music numbers), `voices.py` (speaker clusters), `tag.py` (local vision tags), `film_card.py` (the one-page summary) |
| `3-shorts-clipper/` | Long stream → short clips | `chief/core.py`: job queue (SQLite), run lock, STOP file, RAM/VRAM/disk checks, daily Claude call budget, Telegram notify. `moments/finder.py`: 25–62 s windows that start and end on whole sentences, scored on loudness bursts, reaction words, hook, payoff, speech density, dead air, filler and on-screen motion. `cutter/cutter.py`: FFmpeg vertical layout + karaoke captions + loudness normalisation. `writer/writer.py`: copy + Voice Check |
| `4-clip-study/` | Learning what makes clips work | `cycle.py`: resumable study of 1,000+ Shorts (download, top comments without usernames, frames, framing, audio, contact sheet, then the video is deleted). `build_training.py`, `measure_hooks.py`: human labels joined to measurements as a training table |
| `example-output/` | Real outputs | `film_card.md` of an 18:50 documentary; `reel_timeline.md` of a 30 s reel |

## Models and tools (all free; only Claude is a paid subscription)

| Job | Tool | Runs |
|---|---|---|
| Download | yt-dlp (+ Node.js for YouTube's JavaScript challenge) | local |
| Audio/video split, proxy, cutting, captions burn-in, loudness | FFmpeg (NVENC on the GPU for the proxy) | local |
| Speech to text with word timings | faster-whisper `large-v3` (GPU float16, CPU int8 fallback); `small` / `large-v3-turbo` for bulk | local |
| Voice / music separation | Demucs `htdemucs` (two stems) | local GPU |
| Speaker clusters | SpeechBrain ECAPA embeddings + hierarchical clustering | local |
| Cuts | PySceneDetect AdaptiveDetector | local |
| Camera motion, colour | OpenCV Farneback optical flow, k-means palette | local |
| Frame tagging | Qwen3-VL 8B instruct (community build) through Ollama, JSON output, temperature 0 | local GPU |
| Judging moments in bulk | Qwen3 4B (Ollama) as the fallback judge | local |
| Final judgement, writing | Claude Haiku (scoring 25 moments in ONE call), Claude Sonnet (copy), headless `claude -p` | subscription, capped per night |
| On-screen text | Windows OCR at 4 frames/s | local |

## Why it is cheap in tokens (measured)

Real numbers from the 18:50 documentary in `example-output/` (256 shots). Image tokens use Claude's ≈ width×height/750 rule; text tokens use bytes ÷ 4:

| What Claude would read | Tokens |
|---|---|
| One frame per second, one by one, plus the raw data files | ≈ 791,000 |
| One frame per shot, one by one, plus the raw data files | ≈ 255,000 |
| **The pipeline's output: film card + transcript + 6 contact sheets** | **≈ 18,000** (2–7% of the above) |

## How downloading works, and how it stays unblocked

yt-dlp is an open-source downloader. For each link it loads the page or the platform's API, finds the stream addresses (YouTube needs a small JavaScript challenge solved, which is why Node.js is installed), picks a format (`bv*[height<=1080]+ba/b` = best video up to 1080p plus best audio), downloads both and merges them with FFmpeg. With `--write-info-json` it also saves the title, views, chapters, "most replayed" heatmap and (optionally) top comments.

Platforms block clients that look like bots. The pipeline avoids that by behaving like one careful person, not by hiding:
- **A real, logged-in session.** Cookies are exported from the user's own browser with an open-source extension, so requests carry a normal session. No password is ever typed into a script. The cookie file lives outside every repo and is never printed.
- **A home connection.** Platforms distrust datacentre IP addresses much more than home ones, so everything runs on the user's PC, not a server.
- **Slow and one at a time.** At least 30 s between Instagram downloads, short random sleeps between YouTube clips, one download and one analysis at a time.
- **Back off on a warning, stop on a block.** HTTP 429 = pause 10 minutes. A login challenge, "sign in to confirm" or a 403 writes a `STOP` file and the run ends until a human looks.
- **Certificates done right.** An antivirus that re-signs HTTPS breaks Python downloads. The fix is the operating system's certificate store (`truststore`), never switching certificate checks off.
- **Collect only what is needed.** Comments are kept as text and like counts only, with no usernames. Local videos are deleted after analysis; only frames, numbers and transcripts stay.

## Running one film

```powershell
.\2-film-analysis\run_film.ps1 -Id <youtube_id> -Stage cpu   # download, audio, proxy, shots, sheets
.\2-film-analysis\run_film.ps1 -Id <youtube_id> -Stage gpu   # Whisper, Demucs, audio report, voices, vision tags
py -3.13 .\2-film-analysis\film_card.py work\<youtube_id>     # one-page summary
```

Paths in the scripts are the authors' machine paths, kept as they were so the code is shown exactly as it ran. Change them before running elsewhere. Model weights (Whisper, Demucs, ECAPA, Qwen) download on first use and are not stored in this repository.
