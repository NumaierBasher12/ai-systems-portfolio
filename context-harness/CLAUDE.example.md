# Global assistant rules (pattern; the real file is private)

The global rules file is loaded by every session on every PC. It does not hold project knowledge; it points to the harness.

```markdown
## How to work
- Never resume or re-derive project context from the chat transcript. Context source = the Obsidian folder.
- Before any work on a project: find its folder, read STATE.md then run-log.md, and stop.
  If a fact is not in those notes, ask or check the files.
- After each step, write what changed back into run-log.md / STATE.md, so the notes stay the single source of truth.

## Harness
- Read the central Harness/Preferences.md and Harness/Vault Registry.md at the start of work,
  then the vault's own _Harness/Resume Protocol.md.
- After each work chunk: record in the run-log, put each failure or correction in the right Lessons file,
  promote repeats to a script or checklist item, run the Eval Checklist before saying done.

## Shared vault repos
- Pull with `git pull --rebase --autostash` before work in a vault.
- After each work chunk: stage only the notes changed, scan for secrets, commit "<message> - <Device>", push.
- Conflict: stop and report, never force-push.

## Device label
- At the start of every chat, run `hostname` and label the chat "<topic> - <Account> - <Device>".
```
