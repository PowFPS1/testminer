"""
extractor.py

grabs roblox files from their cdn and saves them
also scrapes the web js bundles because thats where half the leaks come from lol

outputs:
  InExperience/  - windows client stuff (corescripts, locales)
  UniversalApp/  - mobile/uwp client stuff
  JS/            - all the web js bundles
"""

import io
import os
import re
import sys
import json
import shutil
import hashlib
import zipfile
import requests

# roblox cdns and apis
CDN_BASE        = "https://setup.rbxcdn.com"
VERSION_API_WIN = "https://clientsettings.roblox.com/v2/client-version/WindowsPlayer"
VERSION_API_UWP = "https://clientsettings.roblox.com/v2/client-version/WindowsUniversal"

# only care about these file types, skip textures/sounds/whatever
TRACKED_EXTENSIONS = {".luau", ".lua", ".csv", ".json", ".txt", ".xml"}

# windows client packages - extracontent-translations is the one with the locale csvs
# thats the important one, CoreScriptLocalization.csv lives in there
WIN_PACKAGES = [
    "extracontent-luapackages.zip",
    "extracontent-translations.zip",  # <-- this is the one with en-us strings etc
    "content-terrain.zip",
    "RobloxApp.zip",
]

# same but for the mobile/uwp client
# sometimes gets stuff before the windows client does btw
UWP_PACKAGES = [
    "extracontent-luapackages.zip",
    "extracontent-translations.zip",
    "content-terrain.zip",
    "RobloxApp.zip",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# pages to scrape js bundle urls from
WEB_PAGES = [
    "https://www.roblox.com",
    "https://www.roblox.com/catalog",
    "https://www.roblox.com/home",
    "https://www.roblox.com/games",
]

NAMED_BUNDLES_BASE = "https://js.rbxcdn.com"

# these locale js endpoints are goldmine for string leaks
# like thats literally where the classic unlock 20 years of roblox strings came from lol
DYNAMIC_LOCALE_ENDPOINTS = [
    f"{NAMED_BUNDLES_BASE}/DynamicLocalizationResourceScript_Feature",
    f"{NAMED_BUNDLES_BASE}/DynamicLocalizationResourceScript_CommonUI.Features",
    f"{NAMED_BUNDLES_BASE}/DynamicLocalizationResourceScript_Common.GameSorts",
    f"{NAMED_BUNDLES_BASE}/DynamicLocalizationResourceScript_Purchasing.PurchaseDialog",
    f"{NAMED_BUNDLES_BASE}/DynamicLocalizationResourceScript_IAPExperience.PurchaseError",
    f"{NAMED_BUNDLES_BASE}/DynamicLocalizationResourceScript_Authentication",
    f"{NAMED_BUNDLES_BASE}/DynamicLocalizationResourceScript_Notifications.NotificationStream",
]

# state file to track which js bundles have changed between runs
JS_STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "js_bundle_state.json")


def get(url, stream=False, timeout=30):
    """simple wrapper around requests.get, returns None if it fails instead of crashing"""
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, stream=stream)
        r.raise_for_status()
        return r
    except requests.RequestException as e:
        print(f"  warn: couldnt fetch {url} - {e}")
        return None


def get_version(api_url):
    """hit the roblox version api and get the current version hash"""
    r = get(api_url)
    if not r:
        return None
    return r.json().get("clientVersionUpload")


def load_js_state():
    """load the saved js bundle hashes so we can see whats changed"""
    if os.path.exists(JS_STATE_FILE):
        with open(JS_STATE_FILE) as f:
            return json.load(f)
    return {}


def save_js_state(state):
    with open(JS_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def extract_package(version, package_name, out_dir):
    """
    downloads a zip from the roblox cdn and pulls out the files we care about
    the zips can have backslash paths on windows so we normalise those
    """
    url = f"{CDN_BASE}/{version}-{package_name}"
    print(f"  downloading {package_name}...")
    r = get(url, timeout=60)
    if not r:
        return 0

    count = 0
    try:
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            for zip_path in zf.namelist():
                # roblox zips sometimes have backslash paths, normalise them
                zip_path_norm = zip_path.replace("\\", "/").strip("/")
                if not zip_path_norm:
                    continue
                _, ext = os.path.splitext(zip_path_norm)
                if ext.lower() not in TRACKED_EXTENSIONS:
                    continue
                dest = os.path.join(out_dir, package_name.replace(".zip", ""), *zip_path_norm.split("/"))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(zip_path) as src, open(dest, "wb") as dst:
                    dst.write(src.read())
                count += 1
    except zipfile.BadZipFile:
        print(f"  warn: {package_name} wasnt a zip, skipping")
        return 0

    print(f"  got {count} files from {package_name}")
    return count


def extract_local_client(version, versions_path, out_dir):
    """
    if roblox is installed locally, copy files from it too
    this gets corescripts which arent on the public cdn
    only runs locally, github actions skips this since theres no roblox installed there
    """
    if not versions_path or not os.path.exists(versions_path):
        return 0

    version_folder = None
    for name in os.listdir(versions_path):
        if version in name:
            version_folder = os.path.join(versions_path, name)
            break

    if not version_folder:
        print(f"  couldnt find local install for {version}")
        return 0

    print(f"  copying from local install: {version_folder}")
    count = 0
    for root, _, files in os.walk(version_folder):
        for filename in files:
            _, ext = os.path.splitext(filename)
            if ext.lower() not in TRACKED_EXTENSIONS:
                continue
            src = os.path.join(root, filename)
            rel = os.path.relpath(src, version_folder)
            dest = os.path.join(out_dir, "LocalClient", rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(src, dest)
            count += 1

    print(f"  copied {count} files from local install")
    return count


def extract_locales_from_package(version, package_name, base_out_dir, locale_out_dir):
    """
    copies all csvs out of an extracted package into the locales folder
    handles both CoreScriptLocalization.csv and any per-language files like en-us.csv
    """
    pkg_dir = os.path.join(base_out_dir, package_name.replace(".zip", ""))
    if not os.path.exists(pkg_dir):
        return 0

    count = 0
    for root, _, files in os.walk(pkg_dir):
        for filename in files:
            if not filename.endswith(".csv"):
                continue
            src = os.path.join(root, filename)
            dest = os.path.join(locale_out_dir, "Common", filename)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(src, dest)
            count += 1

    return count


def get_bundle_urls():
    """
    scrape roblox pages and collect all js bundle urls
    also adds the known dynamic locale endpoints directly
    these change without a client update so we check them every run
    """
    urls = set(DYNAMIC_LOCALE_ENDPOINTS)

    for page in WEB_PAGES:
        r = get(page, timeout=15)
        if not r:
            continue
        # grab any js src urls from the page html
        found = re.findall(r'src=["\'](https?://[^\'"]+\.js)["\']', r.text)
        for u in found:
            urls.add(u)
        # also grab js.rbxcdn.com urls specifically
        found2 = re.findall(r'(https?://js\.rbxcdn\.com/[^\'"<>\s]+\.js)', r.text)
        for u in found2:
            urls.add(u)

    return list(urls)


def scrape_web_js(js_out_dir):
    """
    download all js bundles and save the ones that changed
    this is how you catch stuff like new feature flags or catalog item names
    that get shipped in the website before the client even updates
    """
    print("  scraping web js bundles...")
    urls = get_bundle_urls()
    print(f"  found {len(urls)} bundle urls")

    old_state = load_js_state()
    new_state = {}
    changes = []

    for url in urls:
        r = get(url, timeout=20)
        if not r:
            continue

        content = r.text
        bundle_hash = hashlib.md5(r.content).hexdigest()
        new_state[url] = bundle_hash

        # mirror the url structure as the file path
        parsed = url.replace("https://", "").replace("http://", "").split("?")[0]
        dest = os.path.join(js_out_dir, parsed)
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        old_hash = old_state.get(url)

        if old_hash != bundle_hash:
            change_type = "new" if old_hash is None else "changed"
            with open(dest, "w", encoding="utf-8", errors="replace") as f:
                f.write(content)
            changes.append((url, change_type, dest))

    save_js_state(new_state)

    changed = [c for c in changes if c[1] == "changed"]
    new_b   = [c for c in changes if c[1] == "new"]
    print(f"  js bundles: {len(changed)} changed, {len(new_b)} new")
    return changes


def run(repo_root, local_roblox_versions=None):
    """
    main function, called by the github actions workflow
    checks for new roblox version, extracts everything, returns what changed
    """
    in_exp_dir    = os.path.join(repo_root, "InExperience")
    universal_dir = os.path.join(repo_root, "UniversalApp")
    js_dir        = os.path.join(repo_root, "JS")
    version_file  = os.path.join(repo_root, "last_version.txt")

    # what was the last version we saw
    old_version = ""
    if os.path.exists(version_file):
        with open(version_file) as f:
            old_version = f.read().strip()

    # check whats live right now
    win_version = get_version(VERSION_API_WIN)
    uwp_version = get_version(VERSION_API_UWP)

    if not win_version:
        print("error: couldnt get windows player version from roblox api")
        sys.exit(1)

    print(f"windows player: {win_version}")
    print(f"windows universal: {uwp_version}")
    print(f"last seen: {old_version or 'nothing yet (first run)'}")

    is_new = win_version != old_version
    if not is_new:
        print("no new version, nothing to do")
        return {"is_new": False, "version": win_version}

    print(f"\nnew version dropped: {old_version} -> {win_version}\n")

    # grab windows player packages
    print("extracting windows player packages...")
    win_tmp = os.path.join(repo_root, "_tmp_win")
    os.makedirs(win_tmp, exist_ok=True)

    for pkg in WIN_PACKAGES:
        extract_package(win_version, pkg, win_tmp)

    # copy from local install if configured (only works when running locally)
    if local_roblox_versions:
        extract_local_client(win_version, local_roblox_versions, in_exp_dir)

    # pull out the locale csvs - CoreScriptLocalization.csv is the important one
    locale_count = 0
    for pkg in WIN_PACKAGES:
        locale_count += extract_locales_from_package(
            win_version, pkg, win_tmp,
            os.path.join(in_exp_dir, "Locales")
        )

    # copy everything else into PatchRoot
    patch_root = os.path.join(in_exp_dir, "PatchRoot")
    os.makedirs(patch_root, exist_ok=True)
    for pkg_name in WIN_PACKAGES:
        pkg_dir = os.path.join(win_tmp, pkg_name.replace(".zip", ""))
        if not os.path.exists(pkg_dir):
            continue
        for root, _, files in os.walk(pkg_dir):
            for filename in files:
                if filename.endswith(".csv"):
                    continue  # already handled above
                src = os.path.join(root, filename)
                rel = os.path.relpath(src, pkg_dir)
                dest = os.path.join(patch_root, pkg_name.replace(".zip", ""), rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copy2(src, dest)

    shutil.rmtree(win_tmp, ignore_errors=True)

    # now do the same for the mobile/uwp client
    if uwp_version:
        print("\nextracting universal app packages...")
        uwp_tmp = os.path.join(repo_root, "_tmp_uwp")
        os.makedirs(uwp_tmp, exist_ok=True)

        for pkg in UWP_PACKAGES:
            extract_package(uwp_version, pkg, uwp_tmp)

        for pkg in UWP_PACKAGES:
            extract_locales_from_package(
                uwp_version, pkg, uwp_tmp,
                os.path.join(universal_dir, "Locales")
            )

        uwp_patch = os.path.join(universal_dir, "PatchRoot")
        os.makedirs(uwp_patch, exist_ok=True)
        for pkg_name in UWP_PACKAGES:
            pkg_dir = os.path.join(uwp_tmp, pkg_name.replace(".zip", ""))
            if not os.path.exists(pkg_dir):
                continue
            for root, _, files in os.walk(pkg_dir):
                for filename in files:
                    if filename.endswith(".csv"):
                        continue
                    src = os.path.join(root, filename)
                    rel = os.path.relpath(src, pkg_dir)
                    dest = os.path.join(uwp_patch, pkg_name.replace(".zip", ""), rel)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    shutil.copy2(src, dest)

        shutil.rmtree(uwp_tmp, ignore_errors=True)

    # scrape web js bundles - runs every time not just on version change
    print("\nscraping web js bundles...")
    js_changes = scrape_web_js(js_dir)

    # save the new version so next run knows what to compare against
    with open(version_file, "w") as f:
        f.write(win_version)

    print(f"\ndone. saved version: {win_version}")
    return {
        "is_new": True,
        "version": win_version,
        "old_version": old_version,
        "uwp_version": uwp_version,
        "js_changes": len(js_changes),
    }


if __name__ == "__main__":
    repo = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..")
    local_path = sys.argv[2] if len(sys.argv) > 2 else None
    result = run(os.path.abspath(repo), local_path)
    print(json.dumps(result, indent=2))
