# AI Systems Portfolio: context harness and video understanding pipeline

Two working systems, co-built and used every day by a two-person student team on ordinary home PCs (16 GB RAM, 6–8 GB NVIDIA GPU). This repository holds the real source code and the documentation. Large model weights, videos, cookies and tokens are kept out on purpose.

| | System | One-line summary |
|---|---|---|
| 1 | [Context harness](context-harness/) | An Obsidian ontology graph that gives an AI assistant long-term, shared memory across PCs and accounts, and learns from its own mistakes |
| 2 | [Video understanding pipeline](video-pipeline/) | yt-dlp + FFmpeg + Whisper + local vision models + Claude: turns long YouTube videos, Instagram Reels and Telegram-sent videos into study notes and short clips |

Who built which component: [`CONTRIBUTORS.md`](CONTRIBUTORS.md).

Full explanations with diagrams: [`docs/AI-Systems-Overview.pdf`](docs/AI-Systems-Overview.pdf). Step-by-step student guide: [`docs/Junior-Guide.pdf`](docs/Junior-Guide.pdf). **Install it on your own PC:** [`SETUP.md`](SETUP.md) (also as [`docs/Setup-Guide.pdf`](docs/Setup-Guide.pdf)).

## 1. Context harness

**Problem.** A large language model forgets everything between sessions. Rebuilding context from old chats is slow and expensive, and only works on one machine.

**Approach.** Knowledge lives in a graph of small Markdown notes (Obsidian). Every note names its parent (`up:`) and links related notes (`[[...]]`), so the vault forms an ontology: index → projects → state, log, protocols; and a central "harness" of preferences, lessons and checklists. A new session reads a fixed, short path (preferences → registry → resume protocol → project state → last lines of the log) and nothing else.

**Relearning.** Every mistake becomes a lesson (symptom, cause, fix). A lesson that happens twice is promoted to a script, a checklist line or a rule, and the checklist runs before any task is called done. New kinds of work first run with the strongest model; the exact protocol is written down, and cheaper models then run it.

**Shared context.** Each vault is a private Git repository, synced with a pull-first, secret-scanned, never-force-push routine. Every PC and every assistant account that clones the repositories starts from the same context.

**Results.** A new session loads 0.4–15% of a mature project's notes (measured on five real vaults). The team estimates overall token use fell to about 37% of the earlier chat-based workflow. The harness supported the team's Model United Nations training work for more than 120 individual delegates.

## 2. Video understanding pipeline

**Problem.** Learning from a long video means watching all of it. A language model cannot watch video, and sending it every frame is far too expensive.

**Approach.** Local tools do the heavy work and write compact files; the language model reads only those.
1. **Intake:** a private Telegram bot, saved Instagram collections, or a channel watch passes the link to **yt-dlp**.
2. **Split on one clock:** **FFmpeg** separates the audio and makes a small proxy video.
3. **Audio:** **faster-whisper large-v3** writes every word with its start and end time; **Demucs** separates voice from music; the scripts measure pace, pauses, pitch, music ducking and sound hits on cuts; **SpeechBrain** clusters the speakers.
4. **Frames:** **PySceneDetect** finds every cut; **OpenCV** optical flow measures camera motion on every frame; a local **Qwen3-VL** vision model tags one frame per shot (medium, shot size, angle, on-screen text, effects); contact sheets show 48 shots per image.
5. **Summary:** one `film_card.md` holds every measurement. **Claude** reads the card, the transcript and the contact sheets, then writes the study document.
6. **Shorts:** for long streams, a moment finder scores 25–62 s windows; Claude Haiku judges the best 25 in one call (a local Qwen model is the fallback); FFmpeg cuts 1080×1920 clips with word-timed captions; Claude Sonnet writes hooks and titles that pass an automatic voice check; a human approves everything before it is used.

**Results.** For an 18:50 documentary, Claude reads about 18,000 tokens instead of 255,000–791,000 (2–7%). The team estimates a student needs about 300–400 seconds with the outputs to grasp a 15-minute video.

## Responsible data collection

The downloaders behave like one careful person, not a crawler: the user's own logged-in session, a home connection, at least 30 seconds between Instagram downloads, a 10-minute pause on any rate-limit warning, and a full stop on a login challenge or block. Comments are stored without usernames, and videos are deleted after analysis. Details: [video-pipeline/README.md](video-pipeline/README.md#how-downloading-works-and-how-it-stays-unblocked).

## Repository map

```
context-harness/     README, note templates, ontology generator, sync script
video-pipeline/
  1-intake/          Telegram bot, collection reader, polling loop
  2-film-analysis/   download, Whisper, shots, audio, voices, vision tags, film card
  3-shorts-clipper/  job queue, moment finder, cutter, writer, review
  4-clip-study/      large-scale Shorts study and training table
  example-output/    a real film card and reel timeline
docs/                the PDF overview and its source
```

## Status of the numbers

- **Measured:** token counts for the film example; resume-load shares for the five vaults (bytes ÷ 4; images ≈ width×height/750).
- **Team estimates:** the 37% overall token figure; 323 seconds (average) to grasp a 15 minute video; 120+ delegates across Dhaka supported.
