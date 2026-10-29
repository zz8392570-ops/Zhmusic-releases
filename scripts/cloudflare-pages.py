#!/usr/bin/env python3
"""Mirror the published SideStore package to Cloudflare Pages (no private source)."""
import copy
import hashlib
import html
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

REPO = 'zz8392570-ops/Zhmusic-releases'
PROJECT = 'zhmusic-download'
IPA = 'ZhMusic-unsigned.ipa'
LIMIT = 25 * 1024 * 1024
WRANGLER = '4.155.0'


def command(*args):
    return subprocess.check_output(args, text=True, timeout=300)


def package_metadata(source):
    apps = source.get('apps', [])
    if len(apps) != 1 or apps[0].get('bundleIdentifier') != 'com.music.player.gyc':
        raise ValueError('Unexpected SideStore application')
    versions = apps[0].get('versions', [])
    if len(versions) != 1:
        raise ValueError('Expected exactly one published version')
    version = versions[0]
    match = re.fullmatch(
        rf'https://github\.com/{re.escape(REPO)}/releases/download/'
        rf'(build-[0-9]{{14}}-[0-9a-f]{{12}})/{re.escape(IPA)}', version.get('downloadURL', ''))
    if not match:
        raise ValueError('Unexpected published download URL')
    if type(version.get('size')) is not int or not 0 < version['size'] <= LIMIT:
        raise ValueError('IPA exceeds Cloudflare Pages 25 MiB asset limit or has invalid size')
    return match[1], version


def verify_package(path, version, asset):
    if path.stat().st_size != version['size'] or asset.get('size') != version['size']:
        raise ValueError('Release asset and SideStore size differ')
    with path.open('rb') as handle:
        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
    if asset.get('digest') != 'sha256:' + digest:
        raise ValueError('GitHub release SHA-256 missing or mismatched')
    with zipfile.ZipFile(path) as archive:
        if archive.testzip():
            raise ValueError('Corrupt IPA')
        names = [n for n in archive.namelist() if n.startswith('Payload/')
                 and n.count('/') == 2 and n.endswith('.app/Info.plist')]
        if len(names) != 1:
            raise ValueError('Expected one main app')
        info = plistlib.loads(archive.read(names[0]))
    if (info.get('CFBundleIdentifier') != 'com.music.player.gyc'
            or info.get('CFBundleShortVersionString') != version.get('version')
            or info.get('CFBundleVersion') != version.get('buildVersion')):
        raise ValueError('IPA version does not match published SideStore source')
    return digest


def rewrite_source(source, base, tag):
    result = copy.deepcopy(source)
    result.update(iconURL=base + '/icon.png', website=base,
                  description='按需构建的 ZhMusic 安装包与 SideStore 更新源。')
    app = result['apps'][0]
    app['iconURL'] = base + '/icon.png'
    app['versions'][0]['downloadURL'] = f'{base}/releases/{tag}/{IPA}'
    return result


def cf_api(account, token, method, suffix='', body=None, allow_missing=False):
    url = f'https://api.cloudflare.com/client/v4/accounts/{account}/pages/projects{suffix}'
    request = urllib.request.Request(url, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        if allow_missing and error.code == 404:
            return None
        raise RuntimeError(f'Cloudflare API HTTP {error.code}; check Account ID and Pages Edit permission') from None
    if not data.get('success'):
        raise RuntimeError('Cloudflare API rejected the request')
    return data['result']


def project_base(account, token):
    project = cf_api(account, token, 'GET', '/' + PROJECT, allow_missing=True)
    if project is None:
        project = cf_api(account, token, 'POST', body={'name': PROJECT, 'production_branch': 'main'})
    if project.get('production_branch') != 'main':
        raise ValueError('Existing Pages project must use main as production branch')
    host = project.get('subdomain', '')
    if not re.fullmatch(r'[a-z0-9-]+\.pages\.dev', host):
        raise ValueError('Cloudflare returned an unexpected Pages subdomain')
    return 'https://' + host


def remote_digest(url, max_size):
    request = urllib.request.Request(url, headers={'Cache-Control': 'no-cache'})
    digest, size = hashlib.sha256(), 0
    with urllib.request.urlopen(request, timeout=45) as response:
        while chunk := response.read(256 * 1024):
            size += len(chunk)
            if size > max_size:
                raise ValueError('Deployed response exceeds expected size')
            digest.update(chunk)
    return digest.hexdigest()


def verify_deployment(base, files):
    # Pages activation may lag behind the successful upload. Check actual bodies.
    for attempt in range(12):
        try:
            for relative, path in files.items():
                expected = hashlib.sha256(path.read_bytes()).hexdigest()
                if remote_digest(f'{base}/{relative}?verify={time.time_ns()}', path.stat().st_size) != expected:
                    raise ValueError(f'Deployed content differs: {relative}')
            return
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            print(f'Public verification attempt {attempt + 1}: {error}', flush=True)
            if attempt == 11:
                raise RuntimeError('Upload finished but public download verification failed; retry Sync Cloudflare Pages') from None
            time.sleep(10)


def main():
    token = os.environ.get('CLOUDFLARE_API_TOKEN', '')
    account = os.environ.get('CLOUDFLARE_ACCOUNT_ID', '')
    if not token or not re.fullmatch('[0-9a-fA-F]{32}', account):
        raise ValueError('Configure CLOUDFLARE_API_TOKEN and the 32-character CLOUDFLARE_ACCOUNT_ID in Actions Secrets')
    with tempfile.TemporaryDirectory(prefix='zhmusic-pages-') as directory:
        root = Path(directory)
        command('gh', 'release', 'download', 'sidestore', '--repo', REPO,
                '--pattern', 'source.json', '--pattern', 'icon.png', '--dir', str(root))
        source = json.loads((root / 'source.json').read_text())
        tag, version = package_metadata(source)
        release = json.loads(command('gh', 'api', f'repos/{REPO}/releases/tags/{tag}'))
        assets = [a for a in release.get('assets', []) if a['name'] == IPA]
        if release.get('draft') or len(assets) != 1:
            raise ValueError('Published IPA missing or ambiguous')
        command('gh', 'release', 'download', tag, '--repo', REPO, '--pattern', IPA, '--dir', str(root))
        verify_package(root / IPA, version, assets[0])
        icon = (root / 'icon.png').read_bytes()
        if not icon.startswith(b'\x89PNG\r\n\x1a\n') or len(icon) > LIMIT:
            raise ValueError('Invalid published icon')
        base = project_base(account, token)
        output = root / 'site'
        package = output / 'releases' / tag / IPA
        package.parent.mkdir(parents=True)
        (root / IPA).replace(package)
        (output / 'icon.png').write_bytes(icon)
        rewritten = rewrite_source(source, base, tag)
        (output / 'source.json').write_text(json.dumps(rewritten, ensure_ascii=False, indent=2) + '\n')
        download = html.escape(rewritten['apps'][0]['versions'][0]['downloadURL'], quote=True)
        (output / 'index.html').write_text(
            '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>ZhMusic 下载</title><h1>ZhMusic</h1>'
            f'<p>版本 {html.escape(version["version"])}</p>'
            f'<p><a href="{download}">下载 IPA</a></p>'
            '<p>SideStore 源地址：</p><p><a href="/source.json">' + base + '/source.json</a></p>'
            '<p>安装需要有效签名。</p></html>')
        (output / '404.html').write_text('<!doctype html><title>404</title>文件不存在')
        (output / '_headers').write_text(
            '/source.json\n  Cache-Control: no-store\n  Content-Type: application/json; charset=utf-8\n'
            '/icon.png\n  Cache-Control: no-cache\n'
            '/releases/*\n  Cache-Control: public, max-age=31536000, immutable\n'
            '  Content-Type: application/octet-stream\n  Content-Disposition: attachment\n')
        subprocess.run(['npx', '--yes', f'wrangler@{WRANGLER}', 'pages', 'deploy', str(output),
                        '--project-name', PROJECT, '--branch', 'main', '--commit-dirty=true'], check=True, timeout=600)
        verify_deployment(base, {'source.json': output / 'source.json', 'icon.png': output / 'icon.png',
                                 f'releases/{tag}/{IPA}': package})
        print(f'Cloudflare verified: {base}/source.json (version {version["version"]})')
        if summary := os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(summary, 'a') as handle:
                handle.write(f'\n### Cloudflare Pages\n\n- SideStore：{base}/source.json\n'
                             f'- 下载页：{base}\n- 版本：{version["version"]}\n'
                             '- 源文件、图标与 IPA 下载内容已校验。\n')


if __name__ == '__main__':
    main()
