---
tags: [harness, lessons]
up: "[[README]]"
---
# Lessons (symptom, cause, fix). Newest first.

A lesson that repeats twice is PROMOTED: a script, an Eval Checklist line, or a Preferences rule.

Real examples from the original vault (shortened):

| Date | Symptom | Cause | Fix / rule |
|---|---|---|---|
| 2026-10-25 | Generated PDFs were handed over with half a page cut off | Only the HTML preview was checked, never the PDF | Render every PDF page to PNG and look at each one before saying done. Promoted to the Eval Checklist |
| 2026-10-25 | A full day spent modelling a 3D shoe from scratch | Nobody searched for an existing open-licence model first | Search open sources first, report licences, build only if nothing usable exists. Promoted (repeat) |
| 2026-10-24 | A local vision tagger called 39% of a film "screen capture"; only 40% correct on 50 checked shots | Tested on 6 frames of one kind; labels had no definitions | Gold set covering every label (5+ each), per-label accuracy, one-line definition per label in the prompt. v2 prompt: 76% |
| 2026-10-24 | A new PC answered a project question from the chat instead of the notes | Vault path one folder deeper; nobody searched for it | A project question starts with: find the vault, read Preferences, Registry, STATE, run-log; cite a note or say "not in the notes" |
| 2026-10-23 | A small local judge gave nearly every clip 7-8/10 | Judge never tested for spread | Test any judge on a clearly good and a clearly bad example first; if scores do not spread, use a stronger model |
| 2026-10-23 | A download failure was reported as "needs login" | Error handler matched common words in an unrelated hint | Print the real error text before trusting a summary; match narrow phrases |
| 2026-10-22 | Python downloads failed with certificate errors | Antivirus re-signs HTTPS; Python's CA list does not trust it | Use the OS certificate store (`truststore`); never disable certificate checks |
