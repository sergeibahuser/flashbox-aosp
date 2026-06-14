#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bootstrap installer for FlashBox AOSP Builder.
One-run installer: creates project files, installs deps, saves token (optional),
optionally builds .exe via PyInstaller and optionally commits & pushes changes.
Works on Windows (but most parts are cross-platform).

Usage:
    python bootstrap.py
"""
import os
import sys
import subprocess
import json
import shutil
import time
from pathlib import Path

try:
    import keyring
except Exception:
    keyring = None

# ---------------------- Config / templates ----------------------
FILES = {
    "requirements.txt": """PyQt6>=6.5
requests>=2.28
keyring>=23.0
""",

    "builder-config.json.example": json.dumps({
        "github": {
            "token": "",
            "owner": "sergeibahuser",
            "repo": "flashbox-aosp",
            "workflow_file": "build-firmware.yml"
        },
        "ui": {"theme": "Fusion"},
        "presets": {
            "example": {
                "rom": "LineageOS",
                "device": "generic_arm64",
                "android_version": "16",
                "build_type": "userdebug",
                "jobs": 8
            }
        }
    }, ensure_ascii=False, indent=2),

    ".github/workflows/build-firmware.yml": """name: Build Firmware

on:
  workflow_dispatch:
    inputs:
      rom:
        description: 'Название кастомной прошивки (например, LineageOS)'
        required: true
        default: 'AOSP'
      rom_repo:
        required: false
      rom_branch:
        required: false
      device_codename:
        description: 'Codename устройства (например, grus для Redmi)'
        required: true
      device_repo:
        required: false
      kernel_repo:
        required: false
      vendor_repo:
        required: false
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
      use_vendor_blobs:
        required: false
        default: 'false'

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
      - name: Checkout workflow repo
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

      - name: Configure ccache
        run: |
          mkdir -p $CCACHE_DIR
          export USE_CCACHE=1
          export CCACHE_DIR=$CCACHE_DIR

      - name: Prepare build workspace
        run: |
          mkdir -p aosp
          cd aosp

      - name: Clone ROM (if provided by rom_repo)
        if: ${{ inputs.rom_repo != '' }}
        run: |
          cd aosp
          git clone --depth 1 ${{ inputs.rom_repo }} rom || true

      - name: Clone device tree (if provided)
        if: ${{ inputs.device_repo != '' }}
        run: |
          cd aosp
          mkdir -p device
          git clone --depth 1 ${{ inputs.device_repo }} device/${{ inputs.device_codename }} || true

      - name: Clone kernel (if provided)
        if: ${{ inputs.kernel_repo != '' }}
        run: |
          cd aosp
          mkdir -p kernel
          git clone --depth 1 ${{ inputs.kernel_repo }} kernel/${{ inputs.device_codename }} || true

      - name: Fetch vendor blobs (if provided)
        if: ${{ inputs.vendor_repo != '' }}
        run: |
          cd aosp
          mkdir -p vendor
          git clone --depth 1 ${{ inputs.vendor_repo }} vendor/${{ inputs.device_codename }} || true

      - name: Run build (best-effort)
        run: |
          set -e
          cd aosp
          if [ -d "rom" ]; then
            cd rom
            if [ -f "build/envsetup.sh" ]; then
              source build/envsetup.sh || true
              lunch ${INPUT_DEVICE_CODENAME}-${INPUT_BUILD_TYPE} || true
              make -j${{ inputs.jobs }} otapackage 2>&1 | tee build.log || true
            else
              echo "Не найден build/envsetup.sh в ROM."
            fi
          else
            echo "ROM repo не передан или не найден."
          fi

      - name: Upload build log
        uses: actions/upload-artifact@v4
        with:
          name: build-log
          path: aosp/rom/build.log

      - name: Upload artifacts
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: firmware
          path: aosp/rom/out || aosp/rom/*.zip || aosp/rom/*.img

      - name: Finalize
        run: echo "Build job completed"
""",

    "scripts/discovery.py": """#!/usr/bin/env python3
# discovery: поиск device-trees и kernel по codename через GitHub Search API
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
        {"vendor":"Xiaomi","model":"Redmi Note 10 Pro","codename":"sweet","first_seen":2021,"common_roms":["LineageOS","AOSP","PixelExperience"]},
        {"vendor":"Samsung","model":"Galaxy S21","codename":"o1s","first_seen":2021,"common_roms":["LineageOS","AOSP","crDroid"]},
        {"vendor":"OnePlus","model":"OnePlus 9","codename":"lemonade","first_seen":2021,"common_roms":["LineageOS","PixelExperience"]}
    ], ensure_ascii=False, indent=2),

    "presets/presets.json": json.dumps({"presets":[{"name":"Lineage - generic","rom":"LineageOS","device":"generic_arm64","android_version":"16","build_type":"userdebug","jobs":8}]}, ensure_ascii=False, indent=2),

    "docs/README_RU.md": "# FlashBox AOSP Builder — Инструкция (на русском)\n\nСм. README в репозитории.\n"
}

# ---------------------- Helpers ----------------------
def run(cmd, cwd=None, shell=False):
    print(">", " ".join(cmd) if isinstance(cmd, list) else cmd)
    try:
        res = subprocess.run(cmd, cwd=cwd, shell=shell, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        print(res.stdout)
        return True, res.stdout
    except subprocess.CalledProcessError as e:
        print("Ошибка:", e.stderr)
        return False, e.stderr


def check_git():
    if shutil.which("git"):
        return True
    return False


def ensure_dir(path: Path):
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)

# ---------------------- Main flow ----------------------
def main():
    print("FlashBox Bootstrap Installer")
    if sys.version_info < (3,8):
        print("Требуется Python >= 3.8")
        return

    base = Path.cwd()
    print("Рабочая директория:", base)
    # Option: clone repo
    use_clone = input("Хотите клонировать репозиторий из GitHub? (если у вас уже есть клон, ответьте n) [y/N]: ").strip().lower() == 'y'
    if use_clone:
        repo_url = input("Введите HTTPS URL репозитория (например https://github.com/sergeibahuser/flashbox-aosp.git): ").strip()
        target = input("Куда клонировать? (путь, по умолчанию 'flashbox-aosp'): ").strip() or "flashbox-aosp"
        ok, out = run(["git", "clone", repo_url, target])
        if not ok:
            print("Не удалось клонировать. Прервано.")
            return
        base = Path(target).absolute()
        print("Клонирован в:", base)

    # Create files
    print("Создаю файлы...")
    for rel, content in FILES.items():
        path = base / rel
        ensure_dir(path.parent)
        # Avoid overwriting existing files without confirmation
        if path.exists():
            ans = input(f"Файл {rel} уже существует. Перезаписать? [y/N]: ").strip().lower() == 'y'
            if not ans:
                print("Пропускаю", rel)
                continue
        path.write_text(content, encoding="utf-8")
        print("Создан", rel)

    # Install requirements
    install_reqs = input("Установить pip-зависимости из requirements.txt? [Y/n]: ").strip().lower()
    if install_reqs in ("", "y"):
        req_file = base / "requirements.txt"
        if req_file.exists():
            print("Устанавливаю зависимости...")
            ok, out = run([sys.executable, "-m", "pip", "install", "-r", str(req_file)])
            if not ok:
                print("Проблемы при установке зависимостей. Смотрите вывод выше.")
        else:
            print("requirements.txt не найден; пропускаю установку.")

    # Save token (keyring)
    save_token = input("Сохранить GitHub PAT в системном keyring (Windows Credential Manager)? [y/N]: ").strip().lower() == 'y'
    if save_token:
        if keyring is None:
            print("keyring не установлен. Устанавливаю...")
            run([sys.executable, "-m", "pip", "install", "keyring"])
            try:
                import keyring as _kr
                globals()['keyring'] = _kr
            except Exception as e:
                print("Не удалось установить keyring:", e)
                keyring = None
        if keyring:
            token = input("Вставьте GitHub PAT (будет сохранён в системе): ").strip()
            keyring.set_password("flashbox_builder_token", "token", token)
            print("Токен сохранён (ключ: flashbox_builder_token).")

    # Offer to build exe via PyInstaller
    build_exe = input("Создать .exe через PyInstaller? (требуется PyInstaller) [y/N]: ").strip().lower() == 'y'
    if build_exe:
        # ensure pyinstaller installed
        ok = shutil.which("pyinstaller") is not None
        if not ok:
            ans = input("PyInstaller не найден. Установить pyinstaller? [y/N]: ").strip().lower() == 'y'
            if ans:
                run([sys.executable, "-m", "pip", "install", "pyinstaller"])
        # find main GUI file to build
        candidates = ["firmware-builder-gui.py", "main.py", "firmware-builder-shell.py"]
        found = None
        for c in candidates:
            p = base / c
            if p.exists():
                found = p
                break
        if found:
            print("Собираю exe из", found)
            run(["pyinstaller", "--noconfirm", "--onefile", "--windowed", "--name", "FlashBoxBuilder", str(found)], cwd=str(base))
            print("Проверьте папку dist/ для FlashBoxBuilder.exe")
        else:
            print("Основной GUI файл не найден. Скопируйте main.py или firmware-builder-gui.py в папку и запустите снова.")

    # Git commit & push (optional)
    do_git = input("Сделать git commit и push изменений в новую ветку? (требуется локальный git и права) [y/N]: ").strip().lower() == 'y'
    if do_git:
        if not shutil.which("git"):
            print("git не найден в PATH. Установите git и повторите.")
        else:
            branch = input("Имя ветки для создания (пример feature/flashbox-templates) [default: feature/flashbox-templates]: ").strip() or "feature/flashbox-templates"
            run(["git", "checkout", "-b", branch], cwd=str(base))
            run(["git", "add", "."], cwd=str(base))
            commit_msg = "Add FlashBox builder templates and docs"
            run(["git", "commit", "-m", commit_msg], cwd=str(base))
            push_ok = run(["git", "push", "--set-upstream", "origin", branch], cwd=str(base))
            if not push_ok[0]:
                print("Push не удался. Возможно защита ветки или требуются права. Вы можете создать PR вручную.")

    print("Bootstrap завершён. Проверьте созданные файлы и README в docs/")
    print("Дальше: установите self-hosted runner для стабильных сборок Android 16+, заполните token в GUI и запустите сборку через приложение.")
    return

if __name__ == "__main__":
    main()
