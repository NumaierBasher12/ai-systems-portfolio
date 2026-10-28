# Context harness: an Obsidian ontology graph that gives an AI assistant long-term memory

A large language model forgets everything between sessions. Rebuilding context by pasting old chats is slow, costs a lot of tokens, and only works on the PC that holds the chat. The harness swaps the chat for a **graph of small notes** that any session can load in a fixed order, so the assistant starts from the same knowledge on any PC and any account.

## 1. The graph (the "ontology")

Every note is a node. Edges come from two places:

| Edge | Written as | Meaning |
|---|---|---|
| Parent | `up: "[[INDEX]]"` in the note's front matter | The note belongs to the note named there, which makes a tree: INDEX → project hub → STATE / run-log / topic notes |
| Cross link | `[[Note name]]` in the text | "This depends on or relates to that" |
| Colour group | Obsidian graph colour per project | Each project shows as its own cluster in the graph view |

```
INDEX (root)
├── Harness/            rules for how the assistant works (all projects)
│   ├── Preferences     how the user wants work done
│   ├── Lessons         every mistake: symptom → cause → fix
│   ├── Eval Checklist  what has to be true before saying "done"
│   └── Vault Registry  every vault and where to resume it
└── Projects/<name>/
    ├── STATE.md        where the project is now (short, always current)
    ├── run-log.md      every step, newest at the end
    └── topic notes     protocols, analyses, decisions
```

Some vaults are **generated** from code instead of written by hand. `ontology-generator/ontology.py` defines a knowledge map (domains → concepts, each with a keyword pattern and a one-line definition). `build_vault.py` matches scraped audience questions to concepts and writes one note per concept, linked to its domain, plus an `Ontology.canvas` overview. To change the map you edit `ontology.py` and rebuild. You never hand-edit the generated notes.

## 2. Context management: what a new session reads

A new session never re-reads the old chat. It reads, in order:

1. `Harness/Preferences.md` and `Harness/Vault Registry.md` (global rules, where everything lives)
2. the vault's own `_Harness/Resume Protocol.md`
3. the project's `STATE.md`
4. the **last 15 lines** of `run-log.md`

Then it stops reading and starts working. Lessons are only opened when something fails.

The same idea applies to data. Hours of video, thousands of frames and long transcripts go through **local tools first** (Whisper, OpenCV, a local Qwen vision model). Those tools write **compact summary files**, and the assistant reads only the summaries (see `../video-pipeline`).

## 3. Relearning: how the system gets better

```
mistake or correction
   → one row in Lessons (symptom, cause, fix)
   → the same lesson appears twice? PROMOTE it:
        a script that makes the mistake impossible, or
        a line in the Eval Checklist, or
        a rule in Preferences
   → Eval Checklist is run before every "done"
```

Two more rules keep it honest:
- **Protocol first, then delegate.** The first rounds of any new kind of job run with the strongest model and the user. During those rounds the exact protocol is written down: every measurement, its script, its thresholds, and what an agent may and may not read. After that, cheaper models run the job from the protocol alone. Every later correction goes back into the protocol.
- **Model log.** Every finished task gets one row: date, task type, model, result, and the user's own rating. Before a new task the log is searched, so the model with the best record for that task type gets picked.

## 4. Several PCs and several accounts, one context

Each vault is a private Git repository. The routine (`scripts/sync-vaults.sh`):
- **Start of work:** `git pull --rebase --autostash`
- **After each chunk of work:** stage only the notes that changed (never `git add -A`, never per-device editor files), scan the diff for keys, tokens and cookies, commit with the device name, and push
- **On a conflict:** stop and report. Never force-push.

The assistant's global rules (`CLAUDE.example.md` shows the pattern) live in a private repo too. So any PC, and any Claude account signed in on it, that clones the repos loads the **same context**. Each chat is labelled with the account and the device, so work from different machines can be told apart.

## 5. Measured effect

How much a new session loads through the resume protocol, compared with reading every note of that project (tokens estimated as bytes ÷ 4, measured 2026-10-29 on the real vaults):

| Project | Resume load | All project notes | Share |
|---|---|---|---|
| Clipping research | 7.7k | 52.8k | 15% |
| Documentary | 4.3k | 48.1k | 9% |
| Web scraping | 5.5k | 126k | 4% |
| MUN Academy | 3.0k | 719k | 0.4% |
| Assistant (young project) | 5.8k | 11.2k | 52% |

The team's overall estimate across mixed daily work (resume plus the reading done during the task) is that token use dropped to about **37%** of the earlier chat-based way of working. That figure is the team's own estimate, not an independent benchmark.

## Files here

| Path | What it is |
|---|---|
| `templates/` | Clean copies of the harness notes, ready to drop into a new vault |
| `ontology-generator/` | The code that builds a generated ontology vault (`ontology.py` = the map, `build_vault.py` = the builder) |
| `scripts/sync-vaults.sh` | The multi-PC sync routine with a secret scan |
| `CLAUDE.example.md` | How the global assistant rules point to the harness |
