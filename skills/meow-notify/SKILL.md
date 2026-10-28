---
name: meow-notify
description: Set up, test, troubleshoot, or remove MeoW phone notifications for completed Codex turns, with optional redacted summaries of the actual request and final reply.
---

# MeoW notifications

Use the bundled Python 3.11+ scripts to configure Codex's user-level `notify` hook. This integration covers `agent-turn-complete`; it does not monitor arbitrary background jobs or create heartbeat schedules.

## Setup

Resolve `scripts/manage.py` relative to this skill. Run `python scripts/manage.py status` first. The installer respects `CODEX_HOME`, preserves another existing notifier, and backs up configuration before editing only the root `notify` key. Do not replace an unrelated notifier by hand.

Obtain the user's private MeoW HTTPS endpoint from their local configuration or a private environment variable. Do not print the URL, put it in shell arguments, commit it, or reuse someone else's destination. A missing endpoint is required setup information.

- `python scripts/manage.py install --mode summary` prompts privately for the endpoint, or reads `MEOW_ENDPOINT`.
- `python scripts/manage.py install --mode status` sends fixed completion status without sending conversation text to a summarizer.
- For unattended installation use `--endpoint-env NAME` with an already populated private variable.

Summary mode matches the actual turn request and final reply. Explain that it makes an additional authenticated `codex exec` call per completed turn, with up to one validation retry and a shared 120-second deadline. It consumes Codex usage and may delay delivery. Use the same configured Codex model; do not silently substitute another provider. Check installed CLI support with `codex exec --help` if summary generation fails. The bundled CLI flags require a recent Codex build.

The worker strips common technical identifiers before summarization, instructs the model to generalize private details, then validates Chinese-only output. Chinese names or private facts can still survive semantic redaction: never promise perfect anonymization. Prefer status mode for conversations that must not be summarized. Do not weaken validation or send raw text to MeoW to fix failures. Summary failure produces an explicit “摘要暂不可用” notification.

## Verify and troubleshoot

- `python scripts/manage.py test` sends one fixed, non-sensitive connectivity test to the configured phone. Only do this when the user requested setup or a notification test.
- `python scripts/manage.py status` shows counts and mode without printing endpoints or message content.
- `python scripts/manage.py retry` explicitly resets failed jobs and restarts pending delivery.
- For an isolated summary preview, pipe a synthetic JSON event containing `input-messages` and `last-assistant-message` to the installed `semantic_notify.py --preview`. It uses the model but does not deliver.

MeoW acceptance means the HTTPS request and application `status=200` succeeded; it does not prove the phone displayed a notification. Delivery makes at most six attempts with backoff. The queue stores validated summaries, never raw events; logs contain only hashes, timestamps and status. Local summaries and backups remain on disk until the user removes them. Restart Codex if its running process has not reloaded configuration. Queue workers resume on the next event, install, or `retry`; there is no boot service.

## Remove

`python scripts/manage.py uninstall` restores the previous notifier only if the current hook still belongs to this installation. It preserves runtime state and configuration backups. Already running workers may finish pending jobs. Do not remove the runtime directory while Codex or its workers are using it.

Sources: [MeoW API](https://www.chuckfang.com/MeoW/api_doc.html), [Codex advanced configuration](https://learn.chatgpt.com/docs/config-file/config-advanced).
