# Teams status on a schedule (macOS)

Sets your Microsoft Teams status automatically:

| When | Status |
|---|---|
| Mon–Fri 9:00 AM – 5:00 PM | Available |
| Mon–Thu 12:30 – 1:30 PM | Away (lunch) |
| Fri 2:00 – 3:00 PM | Away |
| After 5:00 PM, before 9:00 AM, weekends | Away |

It clicks Teams' own status menu through macOS accessibility, so the status is a
real "preferred presence" that Teams keeps showing even while the machine sits
idle. No Microsoft account registration, no API keys, no admin approval.

---

## Install on a new laptop

1. **Copy this whole folder** to the other Mac (anywhere — Documents is fine).

2. **Make sure Teams is installed, signed in, and set to open at login.**

3. **Run the installer** in Terminal:

   ```bash
   bash install.sh
   ```

   It copies the running copy to `~/Library/Application Support/teams-status/`,
   builds a small `TeamsStatus.app` launcher, and registers the schedule with
   launchd.

4. **Grant Accessibility permission — this is the step that actually matters.**

   ```bash
   /usr/bin/python3 "$HOME/Library/Application Support/teams-status/teams_status.py" grant
   ```

   That opens **System Settings › Privacy & Security › Accessibility** and
   reveals `TeamsStatus.app` in Finder. Switch **TeamsStatus** on in the list
   (if it is not listed, drag it in from the Finder window).

   Without this the schedule runs but does nothing — macOS blocks it from
   controlling other apps. Nobody can grant this for you; macOS requires a
   human to tick the box.

5. **Confirm it works unattended:**

   ```bash
   launchctl kickstart -k gui/$UID/com.fayeem.teams-status
   /usr/bin/python3 "$HOME/Library/Application Support/teams-status/teams_status.py" log 5
   ```

   The log line must say `trusted=True`. If it says `trusted=False`, step 4
   did not take.

---

## Day-to-day

Everything below uses the installed copy:

```bash
/usr/bin/python3 "$HOME/Library/Application Support/teams-status/teams_status.py" <command>
```

| Command | What it does |
|---|---|
| `status` | What Teams is showing right now |
| `tick` | Apply whatever the schedule says for right now |
| `set away` | Force a status (`available`, `busy`, `dnd`, `brb`, `away`, `offline`) |
| `plan` | Print the coming week's changes and the trigger times |
| `log 40` | Last 40 log lines |
| `grant` | Reopen the Accessibility settings |

Test a time without waiting for it:

```bash
teams_status.py tick --at "12:45"              # today at 12:45
teams_status.py tick --at "2026-09-04 14:15"   # a specific Friday
```

## Changing your hours

Edit `~/Library/Application Support/teams-status/config.json`, then re-run
`bash install.sh` so the launchd trigger times match the new schedule.

```json
"work_start": "09:00",
"work_end": "17:00",
"breaks": [
  { "days": ["mon","tue","wed","thu"], "start": "12:30", "end": "13:30" },
  { "days": ["fri"], "start": "14:00", "end": "15:00" }
],
"status_break": "away"
```

`status_break`, `status_work`, `status_after_hours` and `status_weekend` each
accept `available`, `busy`, `dnd`, `brb`, `away` or `offline`.

Re-running `install.sh` never overwrites your edited `config.json`
(use `bash install.sh --reset-config` if you want the shipped one back).

## Removing it

```bash
bash install.sh --uninstall
```

Your Teams status stays wherever it was.

---

## How it runs

A launchd LaunchAgent fires at exactly the 20 transition points in your week
(and once at login), not on a polling timer. Every run does the same thing:
work out the right status for *now* and apply it if it differs. So a run that
fires late, or a reboot in the middle of the day, still lands on the correct
status.

Files:

| Path | What |
|---|---|
| `~/Library/Application Support/teams-status/` | The running copy, config, and `TeamsStatus.app` |
| `~/Library/LaunchAgents/com.fayeem.teams-status.plist` | The schedule |
| `~/.config/teams-status/teams-status.log` | Log (rotates at 1 MB) |
| `~/.config/teams-status/state.json` | Last status applied |

## Things worth knowing

- **It briefly brings Teams to the front** at each of the six daily changes,
  then puts your previous app back. Unavoidable when driving the real menu;
  harmless on a dedicated laptop.
- **The screen must be unlocked** at the transition moment. A jiggler keeps the
  Mac awake but does not unlock it. If the screen was locked, the next
  scheduled run (or login) corrects the status.
- **Out of office (purple) is not supported.** That state comes from an Outlook
  calendar event or automatic replies, not from the status menu, and this Teams
  account's calendar is separate from the Google Calendar. Breaks use Away.
- **A Teams update could rename the menu items.** The script verifies by reading
  the status back afterwards, and posts a macOS notification if it could not set
  it, so a silent breakage does not go unnoticed for days. Check `log` if the
  status ever looks wrong.
- **Requires `python3`.** Run `python3 --version` once on a new Mac and accept
  the developer tools prompt if it appears.
