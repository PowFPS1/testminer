# testminer — [github.com/PowFPS1/testminer](https://github.com/PowFPS1/testminer)

Automated Roblox dataminer. Detects client updates, extracts files, diffs them, and posts changes to Discord.

## What it tracks

| Folder | Contents |
|---|---|
| `InExperience/` | WindowsPlayer CoreScripts (`.luau`), Locale CSVs |
| `UniversalApp/` | WindowsUniversal/Mobile CoreScripts, Locale CSVs |
| `JS/` | Web JS bundles from `js.rbxcdn.com` and `assets.create.roblox.com` |

## How it works

1. GitHub Actions runs every 5 minutes
2. Checks `clientsettings.roblox.com` for the latest Roblox version
3. If new — downloads packages from `setup.rbxcdn.com` and extracts tracked files
4. Also scrapes web JS bundles on every run
5. Commits all changes to this repo
6. Posts a Discord alert with highlights (new locale strings, FFlags, etc.)
7. GitHub Actions posts a formatted diff comment on the commit

## Setup (for self-hosting)

Add these secrets in your repo Settings → Secrets → Actions:
- `DISCORD_WEBHOOK_URL` — your Discord webhook URL
