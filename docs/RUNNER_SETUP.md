# Self-hosted GitHub Actions runner — install on Anna's Mac

This is the one-time setup Anna runs on her laptop to bring the pipeline back to a
residential IP class. GitHub-hosted Azure runners are blocked by Reddit's 2025-11
anti-bot policy (verified during the 2026-05-31 prod deploy: all 6 subreddits 403'd
from GH-hosted runs; the same fetches from this Mac return 200). Once registered,
every dispatch of `on-demand-run.yml` or `cron-daily.yml` will execute on this
machine.

Estimated time: **10 minutes**.

---

## 0 · Prerequisites (quick sanity check)

Run these in Terminal. Each line should print something reasonable; if any complains,
stop and ask lil-Dev before proceeding.

```sh
# A. Disk encryption — runner credentials live unencrypted in $HOME unless FileVault is on.
fdesetup status
#   Expect: "FileVault is On."
#   If "Off": System Settings → Privacy & Security → FileVault → Turn On. Wait for
#   encryption to finish (can be hours in background; doesn't block this setup).

# B. The repo + .env are in place.
ls ~/Projects/xhs-ai-ip/system1-app/.env
#   Expect: a path printed. mode should be 600 (rw user-only).

# C. Python 3.11+ is available.
python3 --version
#   Expect: Python 3.11.x or higher. (3.12+ also fine.)
```

---

## 1 · Generate the registration token (GitHub UI)

1. Open https://github.com/annayang293-bot/red-red/settings/actions/runners
2. Click **New self-hosted runner**.
3. Pick **macOS** + **ARM64**. (Anna's Mac is M-series → `uname -m` = `arm64`.)
4. GitHub will show a block of shell commands. **Don't follow them blindly** — they
   re-create the directory and config every time you click the button. Instead, copy
   the **token value** from the `./config.sh ... --token YOUR_TOKEN` line.
   - The token looks like `A123BCDEF...`, ~30 chars.
   - It expires in **1 hour**, so finish steps 2–3 below promptly.

---

## 2 · Install the runner binary

```sh
mkdir -p ~/actions-runner && cd ~/actions-runner

# Get the latest stable runner. The version below (v2.334.0, 2026-04-21) is current as of
# this doc; check https://github.com/actions/runner/releases for any newer release. The arm64
# tarball is ~30 MB.
curl -L -o runner.tar.gz \
  https://github.com/actions/runner/releases/download/v2.334.0/actions-runner-osx-arm64-2.334.0.tar.gz

# Quick checksum sanity (optional). The GH UI shows the expected SHA-256 alongside
# the download URL; paste it here and compare.
shasum -a 256 runner.tar.gz

tar xzf runner.tar.gz
rm runner.tar.gz
```

If GitHub has shipped a newer release between writing and reading this, update the URL
version number — every other path stays the same.

---

## 3 · Register the runner

```sh
cd ~/actions-runner

./config.sh \
  --url https://github.com/annayang293-bot/red-red \
  --token <PASTE TOKEN FROM STEP 1> \
  --name annas-mac \
  --labels macOS,ARM64 \
  --work _work \
  --unattended
```

Flag-by-flag, so nothing is mysterious:

| Flag | Why |
|---|---|
| `--url` | Repo this runner serves. Single-repo runner — not org-wide. |
| `--token` | One-shot registration token from step 1 (expires in 1h). |
| `--name annas-mac` | Friendly name shown in GitHub UI. Pick anything unique. |
| `--labels macOS,ARM64` | Custom labels that `runs-on: [self-hosted, macOS, ARM64]` will match against. `self-hosted` is added automatically — don't list it. |
| `--work _work` | Directory the runner uses for workflow checkouts. Default; explicit is nicer. |
| `--unattended` | Skips interactive prompts; matches what we just specified. |

**Do NOT pass `--disableupdate`.** Auto-update keeps the runner within GitHub's 30-day
support window. If we ever disable updates, the runner silently stops receiving jobs
after 30 days with no in-band warning.

After this succeeds, the runner appears at
https://github.com/annayang293-bot/red-red/settings/actions/runners with a yellow
"Offline" badge — that's because it's not running yet. The next step fixes that.

---

## 4 · Install + start as a launchd service

```sh
cd ~/actions-runner

# Register the runner as a launchd service (com.github.actions.runner.*.plist under
# /Users/<you>/Library/LaunchAgents/). It will auto-start on boot.
./svc.sh install

# Start it now.
./svc.sh start

# Confirm it's listening.
./svc.sh status
#   Expect:   /Users/annayang/Library/LaunchAgents/actions.runner.annayang293-bot-red-red.annas-mac.plist
#             Started:
#             87123 0 actions.runner.annayang293-bot-red-red.annas-mac
#   (PID will differ. "Started:" with a PID number is what matters.)
```

Then refresh
https://github.com/annayang293-bot/red-red/settings/actions/runners — the badge
should turn green ("Idle"). **Tell lil-Anna when you see green.**

---

## 5 · Verify (let lil-Dev drive)

Don't trigger anything from your side yet. lil-Dev will:
1. Flip `runs-on: ubuntu-latest` → `runs-on: [self-hosted, macOS, ARM64]` in the
   reusable workflow,
2. Push,
3. Trigger one manual dispatch from `https://web-bay-two-26.vercel.app/` or via
   `gh workflow run on-demand-run.yml -f topic='AI 创业'`,
4. Watch the run on the GH UI — you'll see your runner's name (`annas-mac`) under the
   job header,
5. Confirm the resulting Supabase run has Reddit posts again (vs run 52's PH-only).

Total wall-clock for the verify run: ~25–30s once dispatched.

---

## 6 · Choose a sleep policy

The runner can only accept jobs when your Mac is awake. The daily cron fires at
`16:00 UTC = 09:00 PDT / 08:00 PST`. If your laptop is closed at that moment, the
job queues — GitHub holds it for **24 h**, after which it fails.

Pick one:

### (a) Accept the delay (recommended starting point)
Do nothing. Mornings when you open your laptop after 09:00 LA, the runner reconnects
within a minute and immediately picks up the queued cron job. Report lands ~30s later
— so it's "ready by the time you've gone to make coffee."

### (b) Scheduled wake (works with closed lid)
If you want the cron to actually fire at 09:00:

```sh
# 1) Wake every weekday at 08:55 local time so the runner is back online by 09:00.
sudo pmset repeat wakeorpoweron MTWRF 08:55:00

# 2) Optional: let the system go back to sleep ~10 min after waking (default
#    `displaysleep 60` will keep the screen off, and `sleep 1` (current setting) will
#    re-sleep after ~1 min of idleness — that's already fine).

# Verify:
pmset -g sched
#   Expect a line:  wakepoweron at 8:55AM weekdays
```

### (c) Never sleep (NOT recommended for a laptop)
`sudo pmset sleep 0` keeps the Mac awake permanently. Burns battery fast. Only
sensible if the Mac is plugged in at a desk 24/7.

You can switch (a) ↔ (b) any time via `pmset`.

---

## 7 · Enable failure notifications

GitHub will email you when a workflow on the default branch fails — but only if you've
opted in.

1. https://github.com/settings/notifications
2. Under **Actions**, check **Send notifications for failed workflows only**.
3. Optionally set a "Default notifications email" different from your account email if
   you want these routed somewhere specific.

After this, if (post-A2) the daily cron run goes red — whether because Reddit is
having an outage, your Mac was offline for >24h, or the OpenAI key got rotated — you
get an email within minutes instead of finding out next time you open the app.

---

## 8 · Troubleshooting

### "Listening for jobs" never shows up
- `./svc.sh status` — is the service in `Started:` state? If not, `./svc.sh start`.
- `tail -f ~/actions-runner/_diag/Runner_*.log` — last few lines usually point at
  the cause (auth failure, network, version mismatch).

### Runner badge stays yellow ("Offline") on GitHub
- Wait 30 s after `./svc.sh start`. The runner's first heartbeat to GitHub takes a
  few seconds.
- If still offline, `~/actions-runner/_diag/` has connection logs. Most common cause
  on macOS is corporate VPN / proxy blocking outbound to `*.actions.githubusercontent.com`
  on port 443.

### "This runner is using an unsupported version"
- Auto-update should handle this. If it doesn't (e.g. service was stopped over a
  release boundary):
  ```sh
  cd ~/actions-runner
  ./svc.sh stop
  ./config.sh remove --token <NEW REMOVAL TOKEN FROM GH UI>
  # Re-run from step 2 with the new tarball URL.
  ```

### Need to take the runner offline for a while (travel, OS upgrade, etc.)
- `./svc.sh stop` — stops accepting new jobs immediately.
- Currently-running jobs continue to completion.
- `./svc.sh start` to resume.
- For long absences (weeks), `./config.sh remove --token <REMOVAL TOKEN>` and
  re-register on return.

### Fallback when the runner is dead
Pipeline can be run directly from the Mac without the runner:
```sh
cd ~/Projects/xhs-ai-ip/system1-app
python3 -m pipeline.run_once "AI 创业"
```
Writes to the same Supabase project, no GitHub Actions involved. Use when the runner
is down and you need today's report.
