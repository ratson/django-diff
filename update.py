import asyncio
import os
import re
import shutil
import subprocess
import sys
from distutils.version import LooseVersion
from pathlib import Path

import requests


root_path = Path(__file__).parent
tmp_path = Path(os.getenv('TEMP_PATH', root_path.joinpath('tmp')))
repo_path = tmp_path.joinpath('repo')

repo_queue = asyncio.Queue(maxsize=1)
venv_queue = asyncio.Queue(maxsize=4)


def iter_django_versions():
    r = requests.get('https://pypi.org/pypi/Django/json')
    for version, files in r.json()['releases'].items():
        if files and re.match(r'^[0-9.]+$', version):
            yield version


async def prepare_repo():
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
    await venv_queue.put(django_version)
    print(django_version)

    venv_path = tmp_path.joinpath(f'venv_{django_version}')
    python_path = venv_path.joinpath('bin/python')
    pip_path = venv_path.joinpath('bin/pip')
    django_admin_path = venv_path.joinpath('bin/django-admin.py')
    if django_admin_path.exists():
        await repo_queue.put(django_version)
        return

    if LooseVersion(django_version) >= LooseVersion('1.11'):
        await run_command('/usr/local/bin/python3', '-m', 'venv', venv_path)
    else:
        await run_command(
            'virtualenv', '--python', '/usr/local/bin/python2', venv_path)

    await run_command(pip_path, 'install', '-U', 'pip')
    await run_command(pip_path, 'install', f'Django=={django_version}')

    await repo_queue.put(django_version)


def repo_run_command(*args, check=False):
    process = subprocess.run(args, cwd=repo_path, stdout=subprocess.PIPE,
                             check=check)
    return process.stdout.decode(sys.getfilesystemencoding()).strip()


def prepare_branch(django_version):
    venv_path = tmp_path.joinpath(f'venv_{django_version}')
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

    repo_run_command(django_admin_path, 'startproject', 'project')

    settings_path = repo_path.joinpath('project/project/settings.py')
    if not settings_path.exists():
        settings_path = repo_path.joinpath('project/settings.py')
    assert settings_path.exists(), f'unexpected directory structure'

    with settings_path.open() as f:
        settings_text = f.read()
    with settings_path.open('w') as f:
        f.write(re.sub(
            r"SECRET_KEY = '([^']+)'", "SECRET_KEY = '{SECRET_KEY}'",
            settings_text))

    repo_run_command('git', 'add', '--all', '.')
    repo_run_command('git', 'commit', '-m', f'Django v{django_version}')


async def prepare_branches():
    while not venv_queue.empty():
        django_version = await repo_queue.get()
        await venv_queue.get()
        venv_queue.task_done()

        prepare_branch(django_version)

        repo_queue.task_done()


def iter_table_lines(django_versions):
    last_version = None
    for version in sorted(django_versions, key=LooseVersion):
        if last_version is None:
            yield f'| {version} | - | - |'
        else:
            branch_base = f'django/v{last_version}'
            branch_head = f'django/v{version}'
            version_range = '...'.join([branch_base, branch_head])
            compare_url = f'https://github.com/ratson/django-diff/compare/{version_range}'
            stats = repo_run_command('git', 'diff', '--shortstat',
                                     branch_base, branch_head)
            yield f'| {version} | [{version_range}]({compare_url}) | {stats} |'
        last_version = version


def main():
    django_versions = list(iter_django_versions())
    repo_path.mkdir(parents=True, exist_ok=True)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(asyncio.gather(
        prepare_repo(), prepare_branches(),
        *map(prepare_venv, django_versions)))
    loop.close()

    with root_path.joinpath('README.md').open() as f:
        readme_content = f.read()
    m = re.match(r'(.+\| -+ \| -+ \| -+ \|\s+).*\|[^|]+\| - \| - \|(.+)',
                 readme_content, re.DOTALL)
    readme_content = ''.join([
        m.group(1),
        '\n'.join(reversed(list(iter_table_lines(django_versions)))),
        m.group(2),
    ])
    with root_path.joinpath('README.md').open('w') as f:
        f.write(readme_content)


if __name__ == '__main__':
    main()
