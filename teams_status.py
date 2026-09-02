#!/usr/bin/env python3
"""
Drive the Microsoft Teams status on macOS from a weekly schedule.

Works by clicking Teams' own status menu through the macOS accessibility API,
so the status is a real "preferred presence" that Teams keeps showing even
while the machine is idle. No Microsoft account/API registration involved.

Commands:
    tick            apply whatever status the schedule says for right now
    set <status>    force one status (available|busy|dnd|brb|away|offline)
    status          print the status Teams is currently showing
    plan            print the coming week's transitions
    grant           open the Accessibility settings so the schedule can run
    log [n]         show the last n log lines (default 40)
    plist           print the launchd plist derived from config.json

Options:
    --at "HH:MM"              pretend it is that time today (for testing)
    --at "YYYY-MM-DD HH:MM"   pretend it is that date and time
    --force                   apply even if it matches the last applied status
    --quiet                   log only, no stdout
"""

import ctypes
import ctypes.util
import datetime as dt
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
INSTALL_DIR = os.path.expanduser("~/Library/Application Support/teams-status")
APP_BUNDLE = os.path.join(INSTALL_DIR, "TeamsStatus.app")
STATE_DIR = os.path.expanduser("~/.config/teams-status")
STATE_PATH = os.path.join(STATE_DIR, "state.json")
LOG_PATH = os.path.join(STATE_DIR, "teams-status.log")
LOG_MAX_BYTES = 1_000_000


def _config_path():
    """Prefer an explicit override, then the installed copy, then our own folder."""
    override = os.environ.get("TEAMS_STATUS_CONFIG")
    if override:
        return os.path.expanduser(override)
    installed = os.path.join(INSTALL_DIR, "config.json")
    if os.path.isfile(installed):
        return installed
    return os.path.join(HERE, "config.json")


CONFIG_PATH = _config_path()

TEAMS_APP = "Microsoft Teams"
TEAMS_PROC = "MSTeams"

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# our short name -> (menu item to click, acceptable read-backs)
STATUSES = {
    "available": ("Available", ("available",)),
    "busy": ("Busy", ("busy",)),
    "dnd": ("Do not disturb", ("do not disturb", "dnd")),
    "brb": ("Be right back", ("be right back", "brb")),
    "away": ("Appear away", ("away", "appear away")),
    "offline": ("Appear offline", ("offline", "appear offline")),
}


# --------------------------------------------------------------------------
# logging
# --------------------------------------------------------------------------

QUIET = False
RUN_ID = ""


def _rotate_log():
    """Keep the log from growing without bound: one generation of history."""
    try:
        if os.path.getsize(LOG_PATH) < LOG_MAX_BYTES:
            return
    except OSError:
        return
    try:
        os.replace(LOG_PATH, LOG_PATH + ".1")
    except OSError:
        pass


def log(msg, level="INFO"):
    line = "%s  %-5s %s%s" % (
        dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        level,
        RUN_ID,
        msg,
    )
    os.makedirs(STATE_DIR, exist_ok=True)
    _rotate_log()
    try:
        with open(LOG_PATH, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    if not QUIET:
        print(line)


def warn(msg):
    log(msg, "WARN")


def error(msg):
    log(msg, "ERROR")


def start_run(command, args):
    """One header line per invocation so the log reads as discrete runs."""
    global RUN_ID
    RUN_ID = "[%s] " % os.getpid()
    # launchd sets XPC_SERVICE_NAME to the job label for jobs it starts.
    how = "launchd" if "teams-status" in os.environ.get("XPC_SERVICE_NAME", "") else "manual"
    log("---- %s (%s) python=%s trusted=%s"
        % (" ".join([command] + list(args)), how,
           ".".join(str(n) for n in sys.version_info[:3]),
           bool(axlib.AXIsProcessTrusted())))


def notify(title, message):
    if not CONFIG.get("notify_on_failure", True):
        return
    script = 'display notification %s with title %s' % (
        json.dumps(message), json.dumps(title))
    subprocess.run(["osascript", "-e", script], capture_output=True)


# --------------------------------------------------------------------------
# macOS accessibility plumbing (ctypes; nothing outside the stdlib)
# --------------------------------------------------------------------------

cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
axlib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("ApplicationServices"))

CFStringRef = ctypes.c_void_p
CFTypeRef = ctypes.c_void_p
UTF8 = 0x08000100

cf.CFStringCreateWithCString.restype = CFStringRef
cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
cf.CFStringGetLength.restype = ctypes.c_long
cf.CFStringGetLength.argtypes = [CFStringRef]
cf.CFStringGetCString.restype = ctypes.c_bool
cf.CFStringGetCString.argtypes = [CFStringRef, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
cf.CFGetTypeID.restype = ctypes.c_ulong
cf.CFGetTypeID.argtypes = [CFTypeRef]
cf.CFStringGetTypeID.restype = ctypes.c_ulong
cf.CFArrayGetTypeID.restype = ctypes.c_ulong
cf.CFArrayGetCount.restype = ctypes.c_long
cf.CFArrayGetCount.argtypes = [CFTypeRef]
cf.CFArrayGetValueAtIndex.restype = CFTypeRef
cf.CFArrayGetValueAtIndex.argtypes = [CFTypeRef, ctypes.c_long]

axlib.AXUIElementCreateApplication.restype = CFTypeRef
axlib.AXUIElementCreateApplication.argtypes = [ctypes.c_int]
axlib.AXUIElementCopyAttributeValue.restype = ctypes.c_int
axlib.AXUIElementCopyAttributeValue.argtypes = [CFTypeRef, CFStringRef, ctypes.POINTER(CFTypeRef)]
axlib.AXUIElementPerformAction.restype = ctypes.c_int
axlib.AXUIElementPerformAction.argtypes = [CFTypeRef, CFStringRef]
axlib.AXIsProcessTrusted.restype = ctypes.c_bool
axlib.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
axlib.AXIsProcessTrustedWithOptions.argtypes = [CFTypeRef]

cf.CFDictionaryCreate.restype = CFTypeRef
cf.CFDictionaryCreate.argtypes = [ctypes.c_void_p, ctypes.POINTER(CFTypeRef),
                                  ctypes.POINTER(CFTypeRef), ctypes.c_long,
                                  ctypes.c_void_p, ctypes.c_void_p]


def ask_for_accessibility():
    """Ask macOS to list us in the Accessibility pane so the user can tick the box."""
    keys = (CFTypeRef * 1)(cfstr("AXTrustedCheckOptionPrompt"))
    values = (CFTypeRef * 1)(ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue"))
    options = cf.CFDictionaryCreate(None, keys, values, 1, None, None)
    return bool(axlib.AXIsProcessTrustedWithOptions(options))

STRING_TYPE_ID = cf.CFStringGetTypeID()
ARRAY_TYPE_ID = cf.CFArrayGetTypeID()


def cfstr(text):
    return cf.CFStringCreateWithCString(None, text.encode("utf-8"), UTF8)


def from_cfstr(ref):
    if not ref:
        return None
    size = cf.CFStringGetLength(ref) * 4 + 8
    buf = ctypes.create_string_buffer(size)
    if cf.CFStringGetCString(ref, buf, size, UTF8):
        return buf.value.decode("utf-8", "replace")
    return None


def attr(element, name):
    out = CFTypeRef()
    if axlib.AXUIElementCopyAttributeValue(element, cfstr(name), ctypes.byref(out)) != 0:
        return None
    return out.value


def str_attr(element, name):
    value = attr(element, name)
    if not value:
        return None
    if cf.CFGetTypeID(value) != STRING_TYPE_ID:
        return None
    return from_cfstr(value)


def children(element):
    value = attr(element, "AXChildren")
    if not value or cf.CFGetTypeID(value) != ARRAY_TYPE_ID:
        return []
    return [cf.CFArrayGetValueAtIndex(value, i) for i in range(cf.CFArrayGetCount(value))]


def press(element):
    return axlib.AXUIElementPerformAction(element, cfstr("AXPress")) == 0


def find(element, predicate, depth=0, max_depth=40):
    """Depth-first search for the first element matching predicate."""
    try:
        if predicate(element):
            return element
    except Exception:
        pass
    if depth >= max_depth:
        return None
    for child in children(element):
        hit = find(child, predicate, depth + 1, max_depth)
        if hit is not None:
            return hit
    return None


# --------------------------------------------------------------------------
# talking to Teams
# --------------------------------------------------------------------------

def osa(script):
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True)


def teams_pid():
    out = subprocess.run(["pgrep", "-f", "MacOS/" + TEAMS_PROC + "$"],
                         capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else None


def frontmost_app():
    res = osa('tell application "System Events" to return name of first process '
              'whose frontmost is true')
    return res.stdout.strip() or None


def ensure_teams_running():
    if teams_pid():
        return True
    if not CONFIG.get("launch_teams_if_needed", True):
        warn("Teams is not running and launch_teams_if_needed is false")
        return False
    log("Teams is not running; launching it")
    subprocess.run(["open", "-a", TEAMS_APP], capture_output=True)
    for _ in range(30):
        time.sleep(2)
        if teams_pid():
            time.sleep(8)  # let the window and web view come up
            return True
    error("Teams did not start in time")
    return False


def activate_teams():
    osa('tell application "%s" to activate' % TEAMS_APP)
    time.sleep(2.5)


def send_escape(times=3):
    for _ in range(times):
        osa('tell application "System Events" to key code 53')
        time.sleep(0.4)


def warm_tree():
    """Chromium exposes its accessibility tree lazily; this query switches it on."""
    osa('tell application "System Events" to tell process "%s" to return (count of windows)'
        % TEAMS_PROC)
    time.sleep(1.0)


def teams_app_element():
    pid = teams_pid()
    return axlib.AXUIElementCreateApplication(pid) if pid else None


def find_profile_button(tries=6):
    """The avatar button; its description also carries the current status."""
    for attempt in range(tries):
        app = teams_app_element()
        if app:
            hit = find(app, lambda el: str_attr(el, "AXRole") == "AXButton"
                       and (str_attr(el, "AXDescription") or "").lower().startswith("your profile"))
            if hit is not None:
                return hit
        if attempt < tries - 1:
            warm_tree()
            time.sleep(1.5)
    return None


def read_status():
    """Whatever status Teams is showing right now, as a lowercase string."""
    button = find_profile_button()
    if button is None:
        return None
    desc = str_attr(button, "AXDescription") or ""
    # e.g. "Your profile, status Available "
    marker = "status"
    idx = desc.lower().find(marker)
    text = desc[idx + len(marker):] if idx >= 0 else desc
    return text.strip().strip(",").strip().lower() or None


def apply_status(short_name):
    """Click through the profile menu to set a status. Returns True on success."""
    if short_name not in STATUSES:
        raise SystemExit("unknown status %r; use one of %s"
                         % (short_name, ", ".join(sorted(STATUSES))))
    menu_label, accepted = STATUSES[short_name]
    started = time.time()

    if not ensure_teams_running():
        return False

    previous_app = frontmost_app() if CONFIG.get("restore_focus", True) else None

    activate_teams()
    send_escape()
    warm_tree()

    button = find_profile_button()
    if button is None:
        error("could not find the Teams profile button (is Teams signed in?)")
        return False
    if not press(button):
        error("could not open the profile menu")
        return False
    time.sleep(2.0)

    app = teams_app_element()
    change = find(app, lambda el: str_attr(el, "AXRole") == "AXMenuItem"
                  and "change status" in (str_attr(el, "AXDescription") or "").lower())
    if change is None:
        error("could not find the 'change status' entry in the profile menu")
        send_escape()
        return False
    press(change)
    time.sleep(2.0)

    app = teams_app_element()
    item = find(app, lambda el: str_attr(el, "AXRole") == "AXMenuItem"
                and (str_attr(el, "AXTitle") or "").strip().lower() == menu_label.lower())
    if item is None:
        error("could not find the %r item in the status menu" % menu_label)
        send_escape()
        return False
    press(item)
    time.sleep(2.5)
    send_escape(2)

    shown = read_status()
    ok = shown is not None and any(a in shown for a in accepted)
    took = time.time() - started
    if ok:
        log("set %s (%r) -> Teams shows %r  [%.1fs]"
            % (short_name, menu_label, shown, took))
    else:
        warn("set %s (%r) but Teams shows %r -- did not take  [%.1fs]"
             % (short_name, menu_label, shown, took))

    if previous_app and previous_app != TEAMS_APP:
        osa('tell application "%s" to activate' % previous_app)

    return ok


def set_status_with_retry(short_name):
    if apply_status(short_name):
        return True
    warn("first attempt failed; retrying once")
    time.sleep(3)
    if apply_status(short_name):
        return True
    notify("Teams status automation failed",
           "Could not set Teams to %s. See %s" % (short_name, LOG_PATH))
    return False


# --------------------------------------------------------------------------
# the schedule
# --------------------------------------------------------------------------

def load_config():
    with open(CONFIG_PATH) as fh:
        return json.load(fh)


def parse_hhmm(text):
    hour, minute = text.split(":")
    return int(hour) * 60 + int(minute)


def day_key(when):
    return DAYS[when.weekday()]


def desired_status(when):
    """Which status the schedule calls for at `when`. Returns (status, reason)."""
    day = day_key(when)
    minutes = when.hour * 60 + when.minute

    if day not in [d.lower() for d in CONFIG["work_days"]]:
        return CONFIG.get("status_weekend", "away"), "non-working day"

    start = parse_hhmm(CONFIG["work_start"])
    end = parse_hhmm(CONFIG["work_end"])

    if minutes < start:
        return CONFIG.get("status_after_hours", "away"), "before work hours"
    if minutes >= end:
        return CONFIG.get("status_after_hours", "away"), "after work hours"

    for brk in CONFIG.get("breaks", []):
        if day in [d.lower() for d in brk["days"]]:
            if parse_hhmm(brk["start"]) <= minutes < parse_hhmm(brk["end"]):
                return (CONFIG.get("status_break", "away"),
                        "break %s-%s" % (brk["start"], brk["end"]))

    return CONFIG.get("status_work", "available"), "work hours"


def transition_times():
    """Every (day_key, "HH:MM") the schedule changes at."""
    out = set()
    work_days = [d.lower() for d in CONFIG["work_days"]]
    for day in work_days:
        out.add((day, CONFIG["work_start"]))
        out.add((day, CONFIG["work_end"]))
    for brk in CONFIG.get("breaks", []):
        for day in [d.lower() for d in brk["days"]]:
            if day in work_days:
                out.add((day, brk["start"]))
                out.add((day, brk["end"]))
    return sorted(out, key=lambda p: (DAYS.index(p[0]), parse_hhmm(p[1])))


def read_state():
    try:
        with open(STATE_PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def write_state(status, when):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATE_PATH, "w") as fh:
        json.dump({"last_status": status, "applied_at": when.isoformat()}, fh, indent=2)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def parse_at(text):
    today = dt.date.today()
    text = text.strip()
    for fmt, has_date in (("%Y-%m-%d %H:%M", True), ("%H:%M", False)):
        try:
            parsed = dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
        if has_date:
            return parsed
        return dt.datetime.combine(today, parsed.time())
    raise SystemExit('could not read --at %r; use "HH:MM" or "YYYY-MM-DD HH:MM"' % text)


def cmd_tick(when, force):
    want, reason = desired_status(when)
    state = read_state()
    last = state.get("last_status")
    log("tick at %s (%s): schedule wants %s (%s); last applied %s"
        % (when.strftime("%a %Y-%m-%d %H:%M"), day_key(when), want, reason, last))

    if last == want and not force:
        # Cheap check: only readable while Teams has focus. If we cannot see the
        # status we trust the state file rather than stealing focus for nothing.
        warm_tree()
        shown = read_status()
        if shown is None:
            log("already %s per state file; cannot read Teams from the background, "
                "leaving it alone" % want)
            return 0
        if any(a in shown for a in STATUSES[want][1]):
            log("already %s in Teams; nothing to do" % want)
            return 0
        warn("state file says %s but Teams shows %r; re-applying" % (want, shown))

    if set_status_with_retry(want):
        write_state(want, when)
        return 0
    return 1


def cmd_plan(when):
    print("Schedule from %s\n" % CONFIG_PATH)
    print("  work %s  %s-%s" % ("/".join(CONFIG["work_days"]),
                                CONFIG["work_start"], CONFIG["work_end"]))
    for brk in CONFIG.get("breaks", []):
        print("  break %s  %s-%s -> %s" % ("/".join(brk["days"]), brk["start"],
                                           brk["end"], CONFIG.get("status_break")))
    print("\nStatus through the next 7 days (only changes shown):\n")
    previous = None
    for offset in range(7):
        day = when.date() + dt.timedelta(days=offset)
        printed_day = False
        for minute in range(0, 24 * 60):
            moment = dt.datetime.combine(day, dt.time(minute // 60, minute % 60))
            status, reason = desired_status(moment)
            if status != previous:
                if not printed_day:
                    print("  %s" % day.strftime("%a %Y-%m-%d"))
                    printed_day = True
                print("    %s  ->  %-10s (%s)" % (moment.strftime("%H:%M"), status, reason))
                previous = status
    print("\nlaunchd will fire at these transition points:")
    for day, hhmm in transition_times():
        print("    %s %s" % (day, hhmm))
    return 0


def cmd_plist():
    label = "com.fayeem.teams-status"
    # Run through the app bundle, not python directly: macOS ties Accessibility
    # permission to the executable, and only the bundle is a stable identity.
    launcher = os.path.join(APP_BUNDLE, "Contents", "MacOS", "TeamsStatus")
    # launchd weekday numbers: Sunday=0 ... Saturday=6
    weekday_num = {"sun": 0, "mon": 1, "tue": 2, "wed": 3,
                   "thu": 4, "fri": 5, "sat": 6}
    entries = []
    for day, hhmm in transition_times():
        hour, minute = hhmm.split(":")
        entries.append("        <dict>\n"
                       "            <key>Weekday</key><integer>%d</integer>\n"
                       "            <key>Hour</key><integer>%d</integer>\n"
                       "            <key>Minute</key><integer>%d</integer>\n"
                       "        </dict>"
                       % (weekday_num[day], int(hour), int(minute)))
    print("""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>%s</string>
    <key>ProgramArguments</key>
    <array>
        <string>%s</string>
        <string>tick</string>
        <string>--quiet</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>StartCalendarInterval</key>
    <array>
%s
    </array>
    <key>StandardOutPath</key>
    <string>%s</string>
    <key>StandardErrorPath</key>
    <string>%s</string>
    <key>ProcessType</key>
    <string>Interactive</string>
</dict>
</plist>""" % (label, launcher, "\n".join(entries),
               os.path.join(STATE_DIR, "launchd.out.log"),
               os.path.join(STATE_DIR, "launchd.err.log")))
    return 0


def main():
    global QUIET, CONFIG

    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2

    QUIET = "--quiet" in args
    force = "--force" in args
    when = dt.datetime.now()
    if "--at" in args:
        when = parse_at(args[args.index("--at") + 1])

    command = args[0]

    if command == "grant":
        # Open the right settings pane and show the bundle, so it can be dragged in.
        ask_for_accessibility()
        subprocess.run(["open", "-R", APP_BUNDLE], capture_output=True)
        subprocess.run(["open", "x-apple.systempreferences:com.apple.preference."
                        "security?Privacy_Accessibility"], capture_output=True)
        print("Accessibility settings opened, and TeamsStatus revealed in Finder.\n")
        print("Switch ON 'TeamsStatus' in the list. If it is not there, drag it in")
        print("from the Finder window that just opened:\n")
        print("    %s\n" % APP_BUNDLE)
        print("Then check it worked with:\n")
        print("    launchctl kickstart -k gui/$UID/com.fayeem.teams-status")
        print("    /usr/bin/python3 '%s' log 5"
              % os.path.join(INSTALL_DIR, "teams_status.py"))
        return 0

    if command == "log":
        count = int(args[1]) if len(args) > 1 and args[1].isdigit() else 40
        if not os.path.exists(LOG_PATH):
            print("no log yet at %s" % LOG_PATH)
            return 0
        with open(LOG_PATH) as fh:
            lines = fh.readlines()
        print("".join(lines[-count:]), end="")
        return 0

    CONFIG = load_config()

    if command in ("tick", "set", "status"):
        start_run(command, args[1:])

    if command in ("tick", "set", "status") and not axlib.AXIsProcessTrusted():
        # Registers us in the Accessibility list so there is a box to tick.
        ask_for_accessibility()
        error("not allowed to control the computer. Grant Accessibility access to "
              "TeamsStatus in System Settings > Privacy & Security > Accessibility.")
        notify("Teams status automation blocked",
               "Switch on TeamsStatus in Privacy & Security > Accessibility.")
        return 3

    if command == "tick":
        return cmd_tick(when, force)
    if command == "set":
        if len(args) < 2:
            raise SystemExit("set needs a status: %s" % ", ".join(sorted(STATUSES)))
        ok = set_status_with_retry(args[1])
        if ok:
            write_state(args[1], dt.datetime.now())
        return 0 if ok else 1
    if command == "status":
        # Teams only exposes its status to us while it is focused.
        previous_app = frontmost_app() if CONFIG.get("restore_focus", True) else None
        if ensure_teams_running():
            activate_teams()
            warm_tree()
        shown = read_status()
        if previous_app and previous_app != TEAMS_APP:
            osa('tell application "%s" to activate' % previous_app)
        print(shown if shown else "unknown (could not read Teams)")
        return 0 if shown else 1
    if command == "plan":
        return cmd_plan(when)
    if command == "plist":
        return cmd_plist()

    print(__doc__)
    return 2


CONFIG = {}

if __name__ == "__main__":
    sys.exit(main())
