"""
notifier.py

sends discord alerts when roblox updates
pings pow on everything because why not
"""

import os
import sys
import re
import json
import requests

WEBHOOK_URL = os.environ.get(
    "DISCORD_WEBHOOK_URL",
    "https://discord.com/api/webhooks/1544873563297939526/f09crSijeFmetbIwk0QcwfhpizNS9KTBqXRihZVT5R-3rtNV5yrgu4bs_KzAVOaQ-JKt"
)

PING_USER_ID = "960348815695675404"
PING = f"<@{PING_USER_ID}>"

MAX_FIELD = 1024
MAX_DESC  = 4000


def _post(payload):
    """send to discord, dont crash if it fails"""
    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"  discord post failed: {e}")
        return False


def truncate(text, limit):
    if len(text) <= limit:
        return text
    return text[:limit - 3] + "..."


# patterns to search for in js bundles
# basically anything that looks like a new feature or unreleased item name
INTERESTING_PATTERNS = [
    r'FFlag\w+',           # feature flags - roblox uses these to gate unreleased stuff
    r'DFFlag\w+',          # dynamic feature flags
    r'Features\.[A-Z]\w+', # feature locale keys
    r'ClassicUnlock\w*',   # classic theme stuff (like the 20yr anniversary thing)
    r'PersonalizedBonus\w*',
    r'UGCLimited\w*',
    r'"[A-Z][a-zA-Z]+(Crown|Wings|Sword|Aura|Trail)[^"]{0,40}"',  # item names
    r'BONUS_AVATAR_ITEM\w*',
    r'bundleDetected\w*',
    r'"[A-Z][a-zA-Z]{8,}Upsell"',  # new upsell surfaces
    r'enableSelfie\w+',
]


def extract_interesting(text):
    """pull out strings that are probably interesting from a js bundle"""
    found = set()
    for pattern in INTERESTING_PATTERNS:
        for match in re.findall(pattern, text):
            clean = match.strip('"').strip("'")
            if clean and len(clean) > 4:
                found.add(clean)
    return sorted(found)


def parse_csv_simple(text):
    """parse a Key,Value csv into a dict"""
    result = {}
    lines = text.splitlines()
    for line in lines[1:]:  # skip the header row
        if "," not in line:
            continue
        key, _, val = line.partition(",")
        result[key.strip()] = val.strip().strip('"')
    return result


def diff_locale_csv(old_text, new_text):
    """find whats been added, removed or changed between two versions of a locale csv"""
    old = parse_csv_simple(old_text)
    new = parse_csv_simple(new_text)

    added   = {k: v for k, v in new.items() if k not in old}
    removed = {k: v for k, v in old.items() if k not in new}
    changed = {k: (old[k], new[k]) for k in new if k in old and old[k] != new[k]}

    return {"added": added, "removed": removed, "changed": changed}


def notify_new_version(version, old_version=None, uwp_version=None):
    """roblox dropped a new version, lets gooo"""
    desc = f"**windows player:** `{version}`\n"
    if old_version:
        desc += f"**previous:** `{old_version}`\n"
    if uwp_version:
        desc += f"**universal app:** `{uwp_version}`\n"
    desc += "\nextracting files now..."

    payload = {
        "content": PING,
        "embeds": [{
            "title": "🔍 new roblox version",
            "description": desc,
            "color": 0x00b0f4,
        }]
    }
    _post(payload)


def notify_locale_changes(version, locale_diffs):
    """post the new/changed/removed locale strings - this is the actual leak content"""
    if not locale_diffs:
        return

    all_added   = {}
    all_removed = {}
    all_changed = {}

    for d in locale_diffs:
        all_added.update(d.get("added", {}))
        all_removed.update(d.get("removed", {}))
        all_changed.update(d.get("changed", {}))

    if not all_added and not all_removed and not all_changed:
        return

    fields = []

    if all_added:
        lines = [f"`{k}` → {v}" for k, v in list(all_added.items())[:20]]
        if len(all_added) > 20:
            lines.append(f"... and {len(all_added) - 20} more")
        fields.append({
            "name": f"✅ new strings ({len(all_added)})",
            "value": truncate("\n".join(lines), MAX_FIELD),
            "inline": False
        })

    if all_removed:
        lines = [f"`{k}`" for k in list(all_removed.keys())[:15]]
        if len(all_removed) > 15:
            lines.append(f"... and {len(all_removed) - 15} more")
        fields.append({
            "name": f"🗑️ removed strings ({len(all_removed)})",
            "value": truncate("\n".join(lines), MAX_FIELD),
            "inline": False
        })

    if all_changed:
        lines = [f"`{k}`: {old} → {new}" for k, (old, new) in list(all_changed.items())[:10]]
        if len(all_changed) > 10:
            lines.append(f"... and {len(all_changed) - 10} more")
        fields.append({
            "name": f"✏️ changed strings ({len(all_changed)})",
            "value": truncate("\n".join(lines), MAX_FIELD),
            "inline": False
        })

    payload = {
        "embeds": [{
            "title": "📝 locale string changes",
            "description": f"version `{version}`",
            "color": 0xfee75c,
            "fields": fields[:25],
        }]
    }
    _post(payload)


def notify_js_changes(js_changes):
    """post a summary of changed js bundles + any interesting strings found in them"""
    if not js_changes:
        return

    changed = [(url, path) for url, t, path in js_changes if t == "changed"]
    new_b   = [(url, path) for url, t, path in js_changes if t == "new"]

    fields = []

    # scan changed bundles for anything that looks interesting
    all_interesting = []
    for url, path in changed[:10]:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                content = f.read()
            found = extract_interesting(content)
            if found:
                all_interesting.extend(found[:10])
        except Exception:
            pass

    if all_interesting:
        deduped = list(dict.fromkeys(all_interesting))[:30]
        fields.append({
            "name": "🔍 interesting strings",
            "value": truncate("\n".join(f"`{s}`" for s in deduped), MAX_FIELD),
            "inline": False
        })

    if changed:
        names = [url.split("/")[-1][:60] for url, _ in changed[:15]]
        fields.append({
            "name": f"✏️ changed bundles ({len(changed)})",
            "value": truncate("\n".join(f"`{n}`" for n in names), MAX_FIELD),
            "inline": False
        })

    if new_b:
        names = [url.split("/")[-1][:60] for url, _ in new_b[:10]]
        fields.append({
            "name": f"🆕 new bundles ({len(new_b)})",
            "value": truncate("\n".join(f"`{n}`" for n in names), MAX_FIELD),
            "inline": False
        })

    if not fields:
        return

    payload = {
        "embeds": [{
            "title": "🌐 web bundle changes",
            "color": 0xe67e22,
            "fields": fields[:25],
        }]
    }
    _post(payload)


def notify_summary(version, added, removed, modified, js_changes, repo_url=""):
    """wrap up embed after everything is done"""
    desc = ""
    if repo_url:
        desc = f"[view on github]({repo_url})\n"

    payload = {
        "content": PING,
        "embeds": [{
            "title": "📊 done",
            "description": desc,
            "color": 0x9b59b6,
            "fields": [
                {"name": "version",        "value": f"`{version}`", "inline": False},
                {"name": "✅ files added",  "value": str(added),    "inline": True},
                {"name": "🗑️ files removed","value": str(removed),  "inline": True},
                {"name": "✏️ modified",     "value": str(modified), "inline": True},
                {"name": "🌐 js changes",   "value": str(js_changes),"inline": True},
            ]
        }]
    }
    _post(payload)


def notify_error(message):
    """something broke"""
    payload = {
        "content": PING,
        "embeds": [{
            "title": "❌ something broke",
            "description": truncate(message, MAX_DESC),
            "color": 0xed4245,
        }]
    }
    _post(payload)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: notifier.py <command> [args...]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "new_version":
        notify_new_version(
            version     = sys.argv[2] if len(sys.argv) > 2 else "unknown",
            old_version = sys.argv[3] if len(sys.argv) > 3 else None,
            uwp_version = sys.argv[4] if len(sys.argv) > 4 else None,
        )
    elif cmd == "summary":
        notify_summary(
            version    = sys.argv[2] if len(sys.argv) > 2 else "unknown",
            added      = int(sys.argv[3]) if len(sys.argv) > 3 else 0,
            removed    = int(sys.argv[4]) if len(sys.argv) > 4 else 0,
            modified   = int(sys.argv[5]) if len(sys.argv) > 5 else 0,
            js_changes = int(sys.argv[6]) if len(sys.argv) > 6 else 0,
            repo_url   = sys.argv[7] if len(sys.argv) > 7 else "",
        )
    elif cmd == "error":
        notify_error(" ".join(sys.argv[2:]))
    else:
        print(f"unknown command: {cmd}")
        sys.exit(1)
