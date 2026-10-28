---
tags: [harness, preferences]
up: "[[README]]"
---
# How the user wants the assistant to work (each rule carries the user's words and date in the original)

- **Work from notes, not the chat.** If context gets heavy, write state to the vault and let the session be cleared.
- **Token budget: small local models do the bulk, the large model does the thinking.** Hundreds of frames, hours of audio or long transcripts go through local tools first (Whisper for speech, scene detection for cuts, a local vision model for frame tags, OCR for text) and are written to files. The large model reads only the compact outputs. Never loop it over every frame. State the token cost before a big run.
- **Protocol first, then delegate.** First rounds of a new kind of job run with the strongest model and the user; the exact protocol is written during them; cheaper models run it afterwards; corrections go back into the protocol.
- **Model routing.** Small model where the output has a fixed shape and a script can check it; mid model for judgement and code; top model for plans, protocols and final reviews.
- **Honesty about limits.** Say what is verified, what is a guess, what failed. Inspect the real artefact before explaining how it works.
- **Close the loop.** When a task is finished, update the to-do list, tools list and run-log without being asked.
- **Shared repos keep every PC in sync.** Pull at the start, commit only changed notes after each chunk, scan for secrets, push; never force-push.
- **Canvases for plans.** Plans and maps are Obsidian canvases in the project folder.
