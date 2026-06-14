#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bootstrap_auto.py
Однокомандный автoinstaller для FlashBox AOSP Builder.

Usage:
    python bootstrap_auto.py [--token TOKEN] [--no-clone] [--build-exe] [--git-push] [--branch BRANCH] [--yes]

Описание:
- Клонирует репозиторий (если текущая папка не git).
- Создаёт шаблоны файлов (.github/workflows, scripts, device-db, presets, docs, requirements).
- Устанавливает pip-зависимости (по запросу).
- Сохраняет GitHub PAT в системный keyring (опционально).
- Опционально собирает .exe через PyInstaller.
- Опционально делает git commit & push в новую ветку.

Примечание по безопасности: PAT можно передать через --token или переменную окружения GITHUB_TOKEN.
Скрипт использует keyring для безопасного хранения токена (Windows Credential Manager).
"""

import os
import sys
import subprocess
import json
import shutil
import time
from pathlib import Path
import argparse

try:
    import keyring
except Exception:
    keyring = None

REPO_URL_DEFAULT = "https://github.com/sergeibahuser/flashbox-aosp.git"
REPO_DIR_DEFAULT = "flashbox-aosp"

FILES = {
    "requirements.txt": "PyQt6>=6.5\nrequests>=2.28\nkeyring>=23.0\n",

    "builder-config.json.example": json.dumps({
        "github": {"token": "", "owner": "sergeibahuser", "repo": "flashbox-aosp", "workflow_file": "build-firmware.yml"},
        "ui": {"theme": "Fusion"},
        "presets": {"example": {"rom": "LineageOS", "device": "generic_arm64", "android_version": "16", "build_type": "userdebug", "jobs": 8}}
    }, ensure_ascii=False, indent=2),

    ".github/workflows/build-firmware.yml": """name: Build Firmware
on:
  workflow_dispatch:
    inputs:
      rom:
        description: 'Название кастомной прошивки (например, LineageOS)'
        required: true
        default: 'AOSP'
      device_codename:
        description: 'Codename устройства'
        required: true
      android_version:
        required: true
        default: '16'
      build_type:
        required: true
        default: 'userdebug'
      jobs:
        required: true
        default: '8'
      runner:
        required: true
        default: 'self-hosted'
jobs:
  build:
    runs-on: ${{ inputs.runner }}
    permissions:
      contents: read
      actions: write
    env:
      OUT_DIR: out
      CCACHE_DIR: ${{ runner.temp }}/ccache
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      - name: Setup Java
        uses: actions/setup-java@v4
        with:
          distribution: 'temurin'
          java-version: '17'
      - name: Install prerequisites
        if: runner.os == 'Linux'
        run: |
          sudo apt-get update
          sudo apt-get install -y git python3 python3-pip curl zip unzip repo
      - name: Prepare workspace
        run: |
          mkdir -p aosp
          cd aosp
      - name: Run build (best-effort)
        run: |
          echo "This is a template workflow. Provide rom/device repos via inputs for real builds."
      - name: Upload artifacts
        uses: actions/upload-artifact@v4
        with:
          name: firmware
          path: |
            aosp/rom/out
            aosp/rom/*.zip
            aosp/rom/*.img
""",

    "scripts/discovery.py": """#!/usr/bin/env python3
import requests, sys
GITHUB_API = 'https://api.github.com'
def search(token, q, per_page=10):
    headers = {'Authorization': f'token {token}', 'Accept': 'application/vnd.github.v3+json'}
    r = requests.get(f'{GITHUB_API}/search/repositories', headers=headers, params={'q': q, 'per_page': per_page}, timeout=20)
    r.raise_for_status()
    return r.json().get('items', [])
if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('Usage: discovery.py <token> <codename>')
        sys.exit(1)
    token, codename = sys.argv[1], sys.argv[2]
    print('Searching device trees...')
    items = search(token, f'{codename}+topic:device-tree', 20)
    for it in items:
        print(it['full_name'], it.get('html_url'))
    print('\nSearching kernels...')
    items = search(token, f'{codename}+kernel', 20)
    for it in items:
        print(it['full_name'], it.get('html_url'))
""",

    "device-db/devices.json": json.dumps([
        {"vendor": "Xiaomi", "model": "Redmi Note 10 Pro", "codename": "sweet", "first_seen": 2021, "common_roms": ["LineageOS", "AOSP", "PixelExperience"]},
        {"vendor": "Samsung", "model": "Galaxy S21", "codename": "o1s", "first_seen": 2021, "common_roms": ["LineageOS", "AOSP", "crDroid"]},
        {"vendor": "OnePlus", "model": "OnePlus 9", "codename": "lemonade", "first_seen": 2021, "common_roms": ["LineageOS", "PixelExperience"]}
    ], ensure_ascii=False, indent=2),

    "presets/presets.json": json.dumps({"presets": [{"name": "Lineage - generic", "rom": "LineageOS", "device": "generic_arm64", "android_version": "16", "build_type": "userdebug", "jobs": 8}]}, ensure_ascii=False, indent=2),

    "docs/README_RU.md": "# FlashBox AOSP Builder — Инструкция (на русском)\n\nСмотрите README и gui-файл в репозитории.\n"
}


def run(cmd, cwd=None, shell=False):
    print(">", cmd if isinstance(cmd, str) else " ".join(cmd))
    try:
        res = subprocess.run(cmd, cwd=cwd, shell=shell, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        print(res.stdout)
        return True, res.stdout
    except subprocess.CalledProcessError as e:
        print("ERROR:", e.stderr)
        return False, e.stderr


def ensure_dir(p: Path):
    if not p.exists():
        p.mkdir(parents=True, exist_ok=True)


def is_git_repo(path: Path):
    return (path / ".git").exists()


def write_files(base: Path, overwrite=True):
    for rel, content in FILES.items():
        p = base / rel
        ensure_dir(p.parent)
        if p.exists() and not overwrite:
            print(f"Skip existing {rel}")
            continue
        p.write_text(content, encoding="utf-8")
        print("Created", rel)


def install_requirements(base: Path):
    req = base / "requirements.txt"
    if not req.exists():
        print("requirements.txt not found")
        return False
    print("Installing pip requirements...")
    return run([sys.executable, "-m", "pip", "install", "-r", str(req)], cwd=str(base))


def save_token_in_keyring(token: str):
    if token is None:
        return False
    try:
        if keyring is None:
            run([sys.executable, "-m", "pip", "install", "keyring"])
            import keyring as _kr
            globals()["keyring"] = _kr
        keyring.set_password("flashbox_builder_token", "token", token)
        print("Token saved to system keyring (service=flashbox_builder_token).")
        return True
    except Exception as e:
        print("Failed to save token:", e)
        return False


def build_exe_if_requested(base: Path):
    candidates = ["firmware-builder-gui.py", "main.py", "firmware-builder-shell.py"]
    found = None
    for c in candidates:
        p = base / c
        if p.exists():
            found = p
            break
    if not found:
        print("No GUI entry found for building exe.")
        return False
    if shutil.which("pyinstaller") is None:
        print("PyInstaller not found, installing...")
        ok, _ = run([sys.executable, "-m", "pip", "install", "pyinstaller"], cwd=str(base))
        if not ok:
            return False
    print("Building exe from", found.name)
    ok, _ = run(["pyinstaller", "--noconfirm", "--onefile", "--windowed", "--name", "FlashBoxBuilder", str(found)], cwd=str(base))
    return ok


def git_commit_and_push(base: Path, branch: str):
    if not shutil.which("git"):
        print("git not found in PATH, skipping git push.")
        return False
    ok, _ = run(["git", "checkout", "-b", branch], cwd=str(base))
    if not ok:
        print("Could not create branch; continuing.")
    run(["git", "add", "."], cwd=str(base))
    run(["git", "commit", "-m", "Add FlashBox builder templates and docs (automated)"], cwd=str(base))
    ok, out = run(["git", "push", "--set-upstream", "origin", branch], cwd=str(base))
    return ok


def parse_args():
    p = argparse.ArgumentParser(description="Bootstrap auto installer for FlashBox")
    p.add_argument("--token", help="GitHub PAT (optional). If absent, tries env GITHUB_TOKEN or interactive input.")
    p.add_argument("--no-clone", action="store_true", help="Do not clone default repo; use current directory.")
    p.add_argument("--repo-url", default=REPO_URL_DEFAULT, help="Repo URL to clone if current directory is not a git repo.")
    p.add_argument("--repo-dir", default=REPO_DIR_DEFAULT, help="Directory to clone into (if cloning).")
    p.add_argument("--build-exe", action="store_true", help="Attempt to build .exe via PyInstaller.")
    p.add_argument("--git-push", action="store_true", help="Attempt to git commit & push changes to new branch.")
    p.add_argument("--branch", default="feature/flashbox-templates", help="Branch name for git push.")
    p.add_argument("--yes", "-y", action="store_true", help="Assume yes for prompts.")
    return p.parse_args()


def main():
    args = parse_args()
    cwd = Path.cwd()
    target_dir = cwd
    if not args.no_clone and not is_git_repo(cwd):
        print("Current directory is not a git repo. Will clone default repo.")
        target_dir = cwd / args.repo_dir
        if target_dir.exists():
            print("Target dir exists:", target_dir)
        else:
            ok, _ = run(["git", "clone", args.repo_url, str(target_dir)])
            if not ok:
                print("Clone failed, aborting.")
                return
    print("Using directory:", target_dir)
    write_files(target_dir, overwrite=True)
    if not args.yes:
        ans = input("Install pip requirements now? [Y/n]: ").strip().lower()
        do_install = (ans != "n")
    else:
        do_install = True
    if do_install:
        install_requirements(target_dir)
    token = args.token or os.getenv("GITHUB_TOKEN")
    if not token:
        if args.yes:
            token = None
        else:
            token = input("Enter GitHub PAT (will be stored in system keyring): ").strip() or None
    if token:
        save_token_in_keyring(token)
    else:
        print("No token provided; discovery and workflow triggers will require a token later.")
    if args.build_exe:
        build_exe_if_requested(target_dir)
    # Simplified git-push decision logic: if user asked for git push or used -y (assume yes), proceed
    if args.git_push or args.yes:
        print("Attempting git commit & push...")
        ok = git_commit_and_push(target_dir, args.branch)
        if not ok:
            print("git push failed or not permitted. Create PR manually.")
    else:
        if not args.yes:
            do_push = input("Do git commit & push changes to new branch? [y/N]: ").strip().lower() == "y"
            if do_push:
                git_commit_and_push(target_dir, args.branch)
    print("Bootstrap auto finished. Check", target_dir, "and read docs/README_RU.md")
    print("Next steps: run the GUI (firmware-builder-gui.py) or the built exe (dist/FlashBoxBuilder.exe).")
    return

if __name__ == "__main__":
    main()
