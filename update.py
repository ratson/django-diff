import asyncio
import re
import shutil
import subprocess
import sys
from pathlib import Path

import requests


root_path = Path(__file__).parent
tmp_path = root_path.joinpath('tmp')
repo_path = tmp_path.joinpath('repo')
queue = asyncio.Queue(maxsize=4)


def iter_django_versions():
    r = requests.get('https://pypi.org/pypi/Django/json')
    for version, files in r.json()['releases'].items():
        if files:
            yield version


async def prepare_repo():
    repo_path.mkdir(parents=True, exist_ok=True)
    dot_git_path = repo_path.joinpath('.git')
    if dot_git_path.exists():
        return
    shutil.copytree(root_path.joinpath('.git'), dot_git_path)


async def run_command(*args):
    process = await asyncio.create_subprocess_exec(
        *map(str, args), stdout=asyncio.subprocess.PIPE)
    stdout, stderr = await process.communicate()
    return stdout.decode().strip()


async def prepare_venv(django_version):
    await queue.put(django_version)
    print(django_version)

    venv_path = tmp_path.joinpath(f'vevn_{django_version}')
    python_path = venv_path.joinpath('bin/python')
    pip_path = venv_path.joinpath('bin/pip')
    django_admin_path = venv_path.joinpath('bin/django-admin.py')
    if django_admin_path.exists():
        return

    if django_version >= '1.11':
        await run_command('/usr/local/bin/python3', '-m', 'venv', venv_path)
    else:
        await run_command(
            'virtualenv', '--python', '/usr/local/bin/python2', venv_path)

    await run_command(pip_path, 'install', '-U', 'pip')
    await run_command(pip_path, 'install', f'Django=={django_version}')

    await queue.get()
    queue.task_done()


def repo_run_command(*args, check=False):
    process = subprocess.run(args, cwd=repo_path, stdout=subprocess.PIPE,
                             check=check)
    return process.stdout


def prepare_branch(django_version):
    venv_path = tmp_path.joinpath(f'vevn_{django_version}')
    django_admin_path = venv_path.joinpath('bin/django-admin.py').resolve()
    branch_name = f'django/v{django_version}'
    try:
        repo_run_command('git', 'checkout', '--orphan', branch_name,
                         check=True)
        repo_run_command('git', 'reset', '--hard')
    except subprocess.CalledProcessError:
        repo_run_command('git', 'checkout', '--force', branch_name)

    for p in repo_path.glob('*'):
        if p.name == '.git':
            continue
        if p.is_file():
            p.unlink()
        else:
            shutil.rmtree(p)
    repo_run_command('git', 'clean', '-qfdx')

    if b'[directory]' in repo_run_command(django_admin_path,
                                          'startproject', '--help'):
        repo_run_command(django_admin_path, 'startproject', 'project', '.')
    else:
        repo_run_command(django_admin_path, 'startproject', 'project')

    settings_path = repo_path.joinpath('project/settings.py')
    with settings_path.open() as f:
        settings_text = f.read()
    with settings_path.open('wt') as f:
        f.write(re.sub(
            r"SECRET_KEY = '([^']+)'", "SECRET_KEY = '{SECRET_KEY}'",
            settings_text))

    repo_run_command('git', 'add', '--all', '.')
    repo_run_command('git', 'commit', '-m', f'Django v{django_version}')


def main():
    django_versions = list(iter_django_versions())
    loop = asyncio.get_event_loop()

    loop.run_until_complete(asyncio.gather(
        prepare_repo(), *map(prepare_venv, django_versions)))

    list(map(prepare_branch, django_versions))

    loop.close()


if __name__ == '__main__':
    main()
