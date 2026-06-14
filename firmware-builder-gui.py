#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GUI оболочка для управления сборкой прошивок через GitHub Actions
Русский интерфейс, темы, поиск исходников и запуск workflow_dispatch
"""

import os
import json
import threading
import time
from pathlib import Path
import webbrowser

try:
    import PySimpleGUI as sg
    import requests
except Exception:
    print('Необходимо установить зависимости: pip install -r requirements.txt')
    raise

CONFIG_PATH = Path.home() / '.flashbox_builder_config.json'
DEFAULT_CONFIG = {
    'github': {
        'token': '',
        'owner': 'sergeibahuser',
        'repo': 'flashbox-aosp',
        'workflow_file': 'build-firmware.yml'
    },
    'ui': {
        'theme': 'DarkBlue12'
    }
}

# Популярные кастомные прошивки
POPULAR_ROMS = [
    'LineageOS', 'PixelExperience', 'AOSP', 'ArrowOS', 'crDroid', 'ParanoidAndroid', 'Havoc-OS', 'Seraphic'
]

# Поддерживаемые Android версии (включая 16)
ANDROID_VERSIONS = [str(v) for v in range(8, 17)]  # 8..16

# Предустановленные темы для PySimpleGUI
THEMES = ['DarkBlue12', 'DarkGrey13', 'LightGreen', 'SystemDefault', 'DarkAmber']


def load_config():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        except Exception:
            return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
        return True
    except Exception as e:
        sg.popup_error('Ошибка сохранения конфига:', str(e))
        return False


class GitHubClient:
    def __init__(self, token, owner, repo):
        self.token = token
        self.owner = owner
        self.repo = repo
        self.api = 'https://api.github.com'
        self.headers = {
            'Authorization': f'token {self.token}',
            'Accept': 'application/vnd.github.v3+json'
        }

    def validate_token(self):
        try:
            r = requests.get(f'{self.api}/user', headers=self.headers, timeout=20)
            r.raise_for_status()
            return True, r.json().get('login')
        except Exception as e:
            return False, str(e)

    def get_workflow_id_or_file(self, workflow_file):
        # Получаем workflow по файлу
        try:
            r = requests.get(f'{self.api}/repos/{self.owner}/{self.repo}/actions/workflows/{workflow_file}', headers=self.headers, timeout=20)
            if r.status_code == 200:
                return True, r.json().get('id')
            else:
                return False, r.text
        except Exception as e:
            return False, str(e)

    def trigger_workflow(self, workflow_file, ref='main', inputs=None):
        ok, wf = self.get_workflow_id_or_file(workflow_file)
        if not ok:
            return False, f'Workflow не найден: {wf}'
        endpoint = f'/repos/{self.owner}/{self.repo}/actions/workflows/{workflow_file}/dispatches'
        payload = {'ref': ref}
        if inputs:
            payload['inputs'] = inputs
        try:
            r = requests.post(self.api + endpoint, headers=self.headers, json=payload, timeout=20)
            if r.status_code in (204, 201):
                return True, 'Workflow запущен'
            else:
                return False, r.text
        except Exception as e:
            return False, str(e)

    def latest_run(self):
        try:
            r = requests.get(f'{self.api}/repos/{self.owner}/{self.repo}/actions/runs?per_page=1', headers=self.headers, timeout=20)
            r.raise_for_status()
            runs = r.json().get('workflow_runs', [])
            if runs:
                return True, runs[0]
            return False, 'Запусков не найдено'
        except Exception as e:
            return False, str(e)

    def run_status(self, run_id):
        try:
            r = requests.get(f'{self.api}/repos/{self.owner}/{self.repo}/actions/runs/{run_id}', headers=self.headers, timeout=20)
            r.raise_for_status()
            return True, r.json()
        except Exception as e:
            return False, str(e)

    def artifacts(self, run_id):
        try:
            r = requests.get(f'{self.api}/repos/{self.owner}/{self.repo}/actions/runs/{run_id}/artifacts', headers=self.headers, timeout=30)
            r.raise_for_status()
            return True, r.json().get('artifacts', [])
        except Exception as e:
            return False, str(e)

    def download_artifact(self, artifact_id, dest_path):
        try:
            url = f'{self.api}/repos/{self.owner}/{self.repo}/actions/artifacts/{artifact_id}/zip'
            r = requests.get(url, headers=self.headers, timeout=300, stream=True)
            r.raise_for_status()
            with open(dest_path, 'wb') as f:
                for chunk in r.iter_content(32768):
                    if chunk:
                        f.write(chunk)
            return True, dest_path
        except Exception as e:
            return False, str(e)


# Небольшой модуль поиска исходников через GitHub Search API
# Для расширения см. scripts/discovery.py

def find_repos_by_query(token, query, per_page=10):
    headers = {'Authorization': f'token {token}', 'Accept': 'application/vnd.github.v3+json'}
    api = 'https://api.github.com'
    try:
        r = requests.get(f"{api}/search/repositories?q={query}&per_page={per_page}", headers=headers, timeout=20)
        r.raise_for_status()
        return True, r.json().get('items', [])
    except Exception as e:
        return False, str(e)


# UI

def make_window(config):
    sg.theme(config.get('ui', {}).get('theme', 'DarkBlue12'))

    left_col = [
        [sg.Text('Токен GitHub (PAT):')],
        [sg.Input(config['github'].get('token', ''), key='-TOKEN-', password_char='*', size=(40,1)), sg.Button('Сохранить токен', key='-SAVE-TOKEN-')],
        [sg.Text('Владелец репозитория:'), sg.Input(config['github'].get('owner', ''), key='-OWNER-', size=(30,1))],
        [sg.Text('Репозиторий (куда отправлять сборку):'), sg.Input(config['github'].get('repo', ''), key='-REPO-', size=(30,1))],
        [sg.Text('Workflow файл:'), sg.Input(config['github'].get('workflow_file','build-firmware.yml'), key='-WORKFLOW-', size=(30,1))],
        [sg.HorizontalSeparator()],
        [sg.Text('Выбор ROM:' )],
        [sg.Combo(values=POPULAR_ROMS, default_value=POPULAR_ROMS[0], key='-ROM-')],
        [sg.Text('Устройство (codename или модель):')],
        [sg.Input('', key='-DEVICE-', size=(30,1)), sg.Button('Найти исходники', key='-FIND-')],
        [sg.Text('Android версия:'), sg.Combo(values=ANDROID_VERSIONS, default_value=ANDROID_VERSIONS[-1], key='-ANDROID-')],
        [sg.Text('Тип сборки:'), sg.Combo(values=['user', 'userdebug', 'eng'], default_value='userdebug', key='-TYPE-')],
        [sg.Text('Кол-во потоков (jobs):'), sg.Input('4', key='-JOBS-', size=(6,1))],
        [sg.HorizontalSeparator()],
        [sg.Button('Запустить сборку', key='-BUILD-', size=(20,1)), sg.Button('Статус по Run ID', key='-STATUS-')],
        [sg.Input('', key='-RUNID-', size=(30,1))],
        [sg.Button('Скачать артефакты', key='-DOWNLOAD-'), sg.Input('', key='-DL_DIR-', default_text=str(Path.cwd()))]
    ]

    right_col = [
        [sg.Text('Результаты / Логи', font=('Any', 12, 'bold'))],
        [sg.Multiline('', key='-LOG-', size=(80,25), autoscroll=True, disabled=False)],
        [sg.Text('Тема интерфейса:'), sg.Combo(THEMES, default_value=config.get('ui', {}).get('theme','DarkBlue12'), key='-THEME-'), sg.Button('Применить тему', key='-APPLY-THEME-')]
    ]

    layout = [
        [sg.Column(left_col), sg.VSeparator(), sg.Column(right_col)],
        [sg.Text('Версия GUI: 1.0 — полностью на русском. Поддержка Android 16+ (при наличии исходников).')]
    ]

    return sg.Window('FlashBox — Оболочка сборки прошивок (GitHub)', layout, finalize=True)


def append_log(window, text):
    win_log = window['-LOG-']
    current = win_log.get()
    new = current + f"\n{time.strftime('%Y-%m-%d %H:%M:%S')} - {text}"
    win_log.update(new)


def background_wait_for_completion(client: GitHubClient, run_id: str, window):
    append_log(window, f'Ожидание завершения сборки Run ID={run_id}...')
    start = time.time()
    while True:
        ok, res = client.run_status(run_id)
        if not ok:
            append_log(window, f'Ошибка получения статуса: {res}')
            return
        status = res.get('status')
        conclusion = res.get('conclusion')
        append_log(window, f'Статус: {status} | Заключение: {conclusion}')
        if status == 'completed':
            if conclusion == 'success':
                append_log(window, 'Сборка успешно завершена ✅')
            else:
                append_log(window, f'Сборка завершена с ошибкой: {conclusion} ❌')
            # открыть страницу запуска
            html_url = res.get('html_url')
            if html_url:
                append_log(window, f'Открыть страницу в браузере: {html_url}')
                webbrowser.open(html_url)
            return
        if time.time() - start > 60*60*8:  # 8 часов
            append_log(window, 'Таймаут ожидания (8 часов)')
            return
        time.sleep(30)


def main():
    config = load_config()
    sg.theme(config.get('ui', {}).get('theme', 'DarkBlue12'))
    window = make_window(config)

    while True:
        event, values = window.read()
        if event == sg.WINDOW_CLOSED:
            break

        if event == '-SAVE-TOKEN-':
            token = values['-TOKEN-'].strip()
            if not token:
                sg.popup('Токен пустой')
                continue
            config['github']['token'] = token
            config['github']['owner'] = values['-OWNER-'].strip() or config['github'].get('owner')
            config['github']['repo'] = values['-REPO-'].strip() or config['github'].get('repo')
            save_config(config)
            append_log(window, 'Токен и параметры сохранены локально')

        if event == '-APPLY-THEME-':
            theme = values['-THEME-']
            config['ui']['theme'] = theme
            save_config(config)
            sg.theme(theme)
            window.close()
            window = make_window(config)
            append_log(window, f'Тема применена: {theme}')

        if event == '-FIND-':
            device = values['-DEVICE-'].strip()
            token = values['-TOKEN-'].strip() or config['github'].get('token')
            if not token:
                sg.popup_error('Укажите GitHub token и сохраните его')
                continue
            append_log(window, f'Поиск исходников для: {device} ...')
            query = f'{device}+topic:device-tree+in:name'
            ok, items = find_repos_by_query(token, query)
            if not ok:
                append_log(window, f'Ошибка поиска: {items}')
                continue
            if not items:
                append_log(window, 'Ничего не найдено. Попробуйте другой запрос или кодовое имя устройства.')
                continue
            # Показать диалог с найденными репо
            choices = [f"{it['full_name']} — {it.get('description','') or ''}" for it in items]
            choice = sg.popup_get_text('Найденные репозитории:\n' + '\n'.join(choices[:10]) + '\n\nСкопируйте полное имя репозитория в поле Репозиторий слева, если хотите использовать его.', title='Результаты поиска')
            append_log(window, f'Поиск завершён, найдено {len(items)} репозиториев')

        if event == '-BUILD-':
            token = values['-TOKEN-'].strip() or config['github'].get('token')
            owner = values['-OWNER-'].strip() or config['github'].get('owner')
            repo = values['-REPO-'].strip() or config['github'].get('repo')
            workflow = values['-WORKFLOW-'].strip() or config['github'].get('workflow_file')
            rom = values['-ROM-']
            device = values['-DEVICE-'].strip()
            android_ver = values['-ANDROID-']
            build_type = values['-TYPE-']
            jobs = values['-JOBS-']

            if not token:
                sg.popup_error('Укажите GitHub token и сохраните его')
                continue
            client = GitHubClient(token, owner, repo)
            ok, user = client.validate_token()
            if not ok:
                append_log(window, f'Невалидный токен: {user}')
                continue
            append_log(window, f'Токен валиден, пользователь: {user}')

            inputs = {
                'rom': rom,
                'device': device,
                'android_version': android_ver,
                'build_type': build_type,
                'jobs': jobs
            }
            append_log(window, f'Отправка запроса на сборку в {owner}/{repo}...')
            ok, res = client.trigger_workflow(workflow, ref='main', inputs=inputs)
            if ok:
                append_log(window, 'Workflow успешно запрошен. Через несколько секунд получите Run ID.')
                # Попробовать получить последний run id
                time.sleep(3)
                ok2, run = client.latest_run()
                if ok2:
                    run_id = str(run.get('id'))
                    append_log(window, f'Run ID: {run_id} | {run.get("html_url")}')
                    # Запустить в фоне ожидание
                    t = threading.Thread(target=background_wait_for_completion, args=(client, run_id, window), daemon=True)
                    t.start()
                else:
                    append_log(window, f'Не удалось получить run id: {run}')
            else:
                append_log(window, f'Ошибка запуска workflow: {res}')

        if event == '-STATUS-':
            runid = values['-RUNID-'].strip()
            token = values['-TOKEN-'].strip() or config['github'].get('token')
            owner = values['-OWNER-'].strip() or config['github'].get('owner')
            repo = values['-REPO-'].strip() or config['github'].get('repo')
            if not runid:
                sg.popup('Введите Run ID')
                continue
            client = GitHubClient(token, owner, repo)
            ok, res = client.run_status(runid)
            if ok:
                append_log(window, f"Run {runid}: status={res.get('status')}, conclusion={res.get('conclusion')}, url={res.get('html_url')}")
            else:
                append_log(window, f'Ошибка получения статуса: {res}')

        if event == '-DOWNLOAD-':
            runid = values['-RUNID-'].strip()
            dl_dir = values['-DL_DIR-'].strip() or str(Path.cwd())
            token = values['-TOKEN-'].strip() or config['github'].get('token')
            owner = values['-OWNER-'].strip() or config['github'].get('owner')
            repo = values['-REPO-'].strip() or config['github'].get('repo')
            if not runid:
                sg.popup('Введите Run ID')
                continue
            client = GitHubClient(token, owner, repo)
            ok, arts = client.artifacts(runid)
            if not ok:
                append_log(window, f'Ошибка получения артефактов: {arts}')
                continue
            if not arts:
                append_log(window, 'Артефактов не найдено')
                continue
            Path(dl_dir).mkdir(parents=True, exist_ok=True)
            for art in arts:
                name = art.get('name')
                aid = art.get('id')
                dest = os.path.join(dl_dir, f"{name}.zip")
                append_log(window, f'Скачивание {name}...')
                ok2, res2 = client.download_artifact(aid, dest)
                if ok2:
                    append_log(window, f'Скачан: {dest}')
                else:
                    append_log(window, f'Ошибка скачивания: {res2}')

    window.close()


if __name__ == '__main__':
    main()
