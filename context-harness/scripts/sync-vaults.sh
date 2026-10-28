#!/usr/bin/env bash
# Multi-PC vault sync: pull, then commit and push only the changed notes, after a secret scan.
# usage: sync-vaults.sh <device-name> <vault-dir>...
# Stops on a pull conflict or a secret hit; never force-pushes.
set -u
device="$1"; shift
SECRET='(sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16}|sessionid|auth_token|BEGIN [A-Z ]*PRIVATE KEY|[0-9]{8,10}:[A-Za-z0-9_-]{35})'

for v in "$@"; do
  echo "== $v"
  git -C "$v" pull --rebase --autostash || { echo "   pull conflict: stopped, fix by hand"; continue; }
  # changed or new notes and canvases only; per-device editor state stays out
  files=$(git -C "$v" status --porcelain -- '*.md' '*.canvas' | awk '{print substr($0,4)}' | grep -v '^\.obsidian/')
  [ -z "$files" ] && { echo "   nothing to commit"; continue; }
  if printf '%s\n' "$files" | (cd "$v" && xargs -d '\n' grep -lE "$SECRET" --) ; then  # -l: name the file, never echo the secret
    echo "   SECRET-LIKE TEXT in the file(s) above: nothing committed in $v"; continue
  fi
  printf '%s\n' "$files" | (cd "$v" && xargs -d '\n' git add --)
  git -C "$v" commit -qm "Notes update - $device" && git -C "$v" push -q && echo "   pushed: $(printf '%s\n' "$files" | wc -l) file(s)"
done
