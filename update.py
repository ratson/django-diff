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

venv_queue = asyncio.Queue(maxsize=4)


def iter_django_versions():
    r = requests.get('https://pypi.org/pypi/Django/json')
    for version, files in r.json()['releases'].items():
        if files and re.match(r'^[0-9.]+$', version):
            yield version


def prepare_repo():
    dot_git_path = repo_path.joinpath('.git')
    if dot_git_path.exists():
        return
    repo_path.mkdir(parents=True, exist_ok=True)
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
        await venv_queue.get()
        venv_queue.task_done()
        return

    if LooseVersion(django_version) >= LooseVersion('1.11'):
        await run_command('/usr/local/bin/python3', '-m', 'venv', venv_path)
    else:
        await run_command(
            'virtualenv', '--python', '/usr/local/bin/python2', venv_path)

    await run_command(pip_path, 'install', '-U', 'pip')
    await run_command(pip_path, 'install', f'Django=={django_version}')

    await venv_queue.get()
    venv_queue.task_done()


def repo_run_command(*args, check=False):
    process = subprocess.run(args, cwd=repo_path, stdout=subprocess.PIPE,
                             check=check)
    return process.stdout.decode(sys.getfilesystemencoding()).strip()


def build_tag(django_version):
    return f'django-{django_version}'

def prepare_branch(django_version):
    venv_path = tmp_path.joinpath(f'venv_{django_version}')
    django_admin_path = venv_path.joinpath('bin/django-admin.py').resolve()

    repo_run_command('git', 'reset', '--hard')
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
        settings_text = f.read().replace(f'Django {django_version}',
                                         'Django {VERSION}')
    with settings_path.open('w') as f:
        f.write(re.sub(
            r"SECRET_KEY = '([^']+)'", "SECRET_KEY = '{SECRET_KEY}'",
            settings_text))

    repo_run_command('git', 'add', '--all', '.')
    repo_run_command('git', 'commit', '-m', f'Django v{django_version}')
    repo_run_command('git', 'tag', '-f', build_tag(django_version))


def build_diff_branch(django_versions):
    branch_name = 'diff'
    for branch in repo_run_command('git', 'branch').split('\n'):
        branch = branch.strip('* ')
        if branch == branch_name:
            continue
        repo_run_command('git', 'branch', '-D', branch)
    try:
        repo_run_command('git', 'checkout', '--orphan', branch_name,
                         check=True)
    except subprocess.CalledProcessError:
        repo_run_command('git', 'checkout', '--force', branch_name)
    for django_version in django_versions:
        print(build_tag(django_version))
        prepare_branch(django_version)


def iter_table_lines(django_versions):
    last_version = None
    for version in sorted(django_versions, key=LooseVersion):
        if last_version is None:
            yield f'| {version} | - | - |'
        else:
            diff_base = build_tag(last_version)
            diff_head = build_tag(version)
            version_range = '...'.join([diff_base, diff_head])
            compare_url = f'https://github.com/ratson/django-diff/compare/{version_range}'
            stats = repo_run_command('git', 'diff', '--shortstat',
                                     diff_base, diff_head)
            yield f'| {version} | [{version_range}]({compare_url}) | {stats} |'
        last_version = version


def main():
    django_versions = list(iter_django_versions())
    prepare_repo()

    loop = asyncio.get_event_loop()
    loop.run_until_complete(asyncio.gather(
        *map(prepare_venv, django_versions)))
    loop.close()

    build_diff_branch(django_versions)

    readme_path = root_path.joinpath('README.md')
    with readme_path.open() as f:
        readme_content = f.read()
    m = re.match(r'(.+\| -+ \| -+ \| -+ \|\s+).*\|[^|]+\| - \| - \|(.+)',
                 readme_content, re.DOTALL)
    readme_content = ''.join([
        m.group(1),
        '\n'.join(reversed(list(iter_table_lines(django_versions)))),
        m.group(2),
    ])
    with readme_path.open('w') as f:
        f.write(readme_content)


if __name__ == '__main__':
    main()
