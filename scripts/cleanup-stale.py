#!/usr/bin/env python3
"""Reap expired, explicitly marked disposable repositories; never touch active runs."""
import datetime
import json
import re
import subprocess
import sys

OWNER = 'zz8392570-ops'
PREFIX = 'zhmusic-tmp-build-'
MARKER_PREFIX = 'zhmusic-disposable-v1:'
MIN_AGE = datetime.timedelta(hours=3)


def gh(*args):
    return subprocess.run(['gh', *args], check=True, capture_output=True, text=True, timeout=120).stdout


def metadata_matches(repo, now):
    name = repo.get('name', '')
    match = re.fullmatch(re.escape(PREFIX) + r'(\d{14}-[a-f0-9]{12})', name)
    if not match or repo.get('owner', {}).get('login', '').lower() != OWNER.lower():
        return False
    if repo.get('full_name', '').lower() != (OWNER + '/' + name).lower():
        return False
    if repo.get('private') is not False or repo.get('description') != MARKER_PREFIX + match[1]:
        return False
    if not isinstance(repo.get('id'), int) or isinstance(repo.get('id'), bool):
        return False
    try:
        created = datetime.datetime.fromisoformat(repo['created_at'].replace('Z', '+00:00'))
        timestamp = datetime.datetime.strptime(match[1][:14], '%Y%m%d%H%M%S').replace(tzinfo=datetime.timezone.utc)
        return now - created >= MIN_AGE and abs((created - timestamp).total_seconds()) <= 600
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def no_active_runs(full_name):
    runs = json.loads(gh('api', f'repos/{full_name}/actions/runs?per_page=100'))
    count, items = runs.get('total_count'), runs.get('workflow_runs')
    if not isinstance(count, int) or isinstance(count, bool) or not isinstance(items, list):
        raise ValueError('Missing workflow status')
    if count > 100 or count != len(items):
        raise ValueError('Incomplete workflow status')
    return all(isinstance(run, dict) and run.get('status') == 'completed' for run in items)


def owned_repositories():
    # gh --paginate emits one JSON array per page. Parse each independently;
    # older supported gh versions do not provide --slurp.
    raw = gh('api', '--paginate', 'user/repos?affiliation=owner&per_page=100')
    decoder = json.JSONDecoder()
    offset = 0
    repositories = []
    while offset < len(raw):
        while offset < len(raw) and raw[offset].isspace():
            offset += 1
        if offset == len(raw):
            break
        page, offset = decoder.raw_decode(raw, offset)
        if not isinstance(page, list):
            raise ValueError('Unexpected repository listing')
        repositories.extend(page)
    return repositories


def cleanup(now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    owner = json.loads(gh('api', 'user'))['login']
    if owner.lower() != OWNER.lower():
        raise RuntimeError('Token owner does not match configured owner; no repositories deleted')
    errors = []
    for repo in owned_repositories():
        if not metadata_matches(repo, now):
            continue
        full_name = repo['full_name']
        try:
            current = json.loads(gh('api', 'repos/' + full_name))
            if current.get('id') != repo['id'] or not metadata_matches(current, now):
                print('Skipping changed repository:', full_name)
                continue
            if not no_active_runs(full_name):
                print('Skipping active or unknown workflow state:', full_name)
                continue
            # Verify identity a second time immediately before the destructive call.
            latest = json.loads(gh('api', 'repos/' + full_name))
            if latest.get('id') != repo['id'] or not metadata_matches(latest, now):
                print('Skipping repository changed during inspection:', full_name)
                continue
            if not no_active_runs(full_name):
                print('Skipping newly active workflow:', full_name)
                continue
            gh('api', '--method', 'DELETE', 'repos/' + full_name)
            print('Deleted expired temporary repository:', full_name)
        except (subprocess.SubprocessError, ValueError, KeyError) as error:
            errors.append(full_name)
            print('Cleanup failed:', full_name, type(error).__name__, file=sys.stderr)
    if errors:
        raise RuntimeError('Cleanup incomplete: ' + ', '.join(errors))
    print('Cleanup check complete')


if __name__ == '__main__':
    try:
        cleanup()
    except (Exception, KeyboardInterrupt) as error:
        print(str(error) or 'Cleanup interrupted', file=sys.stderr)
        sys.exit(1)
