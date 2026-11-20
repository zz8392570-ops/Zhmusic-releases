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
APP_ID = 'com.zz8392570.zhmusic'
BUILD_TAG = r'build-[0-9]{14}-[0-9a-f]{12}'
PREVIOUS_MIRRORS = 3


def command(*args, timeout=300):
    return subprocess.check_output(args, text=True, timeout=timeout)


def current_app(source):
    apps = source.get('apps', [])
    if not isinstance(apps, list) or any(not isinstance(app, dict) for app in apps):
        raise ValueError('Unexpected SideStore application')
    matches = [app for app in apps if app.get('bundleIdentifier') == APP_ID]
    if len(matches) != 1:
        raise ValueError('Unexpected SideStore application')
    return matches[0]


def package_metadata(source):
    versions = current_app(source).get('versions', [])
    if len(versions) != 1:
        raise ValueError('Expected exactly one published version')
    version = versions[0]
    match = re.fullmatch(
        rf'https://github\.com/{re.escape(REPO)}/releases/download/'
        rf'({BUILD_TAG})/{re.escape(IPA)}', version.get('downloadURL', ''))
    if not match:
        raise ValueError('Unexpected published download URL')
    if type(version.get('size')) is not int or not 0 < version['size'] <= LIMIT:
        raise ValueError('IPA exceeds Cloudflare Pages 25 MiB asset limit or has invalid size')
    return match[1], version


def verified_package_info(path, asset):
    size = asset.get('size')
    if type(size) is not int or not 0 < size <= LIMIT or path.stat().st_size != size:
        raise ValueError('IPA size differs from the release or exceeds the Pages limit')
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
        return plistlib.loads(archive.read(names[0]))


def verify_package(path, version, asset):
    if asset.get('size') != version['size']:
        raise ValueError('Release asset and SideStore size differ')
    info = verified_package_info(path, asset)
    if (info.get('CFBundleIdentifier') != APP_ID
            or info.get('CFBundleShortVersionString') != version.get('version')
            or info.get('CFBundleVersion') != version.get('buildVersion')):
        raise ValueError('IPA version does not match published SideStore source')


def package_asset(release):
    if release.get('draft') or release.get('prerelease'):
        return None
    assets = [asset for asset in release.get('assets', []) if asset['name'] == IPA]
    return assets[0] if len(assets) == 1 else None


def previous_release_assets(current_tag):
    rows = command('gh', 'api', '--paginate', f'repos/{REPO}/releases?per_page=100',
        '--jq', '.[] | {tag_name, draft, prerelease, assets} | tojson')
    result = {}
    for line in rows.splitlines():
        release = json.loads(line)
        tag = release.get('tag_name', '')
        if tag != current_tag and re.fullmatch(BUILD_TAG, tag):
            if asset := package_asset(release):
                result[tag] = asset
    return result


def mirrored_path(tag):
    return f'releases/{tag}/{IPA}'


def download_package(tag, directory, timeout=300):
    directory.mkdir(parents=True, exist_ok=True)
    command('gh', 'release', 'download', tag, '--repo', REPO,
            '--pattern', IPA, '--dir', str(directory), timeout=timeout)
    return directory / IPA


def retain_previous_packages(output, root, releases):
    retained = {}
    for tag, asset in list(releases.items())[:PREVIOUS_MIRRORS]:
        try:
            if type(asset.get('size')) is not int or not 0 < asset['size'] <= LIMIT:
                continue
            path = download_package(tag, root / tag, timeout=90)
            if verified_package_info(path, asset).get('CFBundleIdentifier') != APP_ID:
                continue
            package = output / mirrored_path(tag)
            package.parent.mkdir(parents=True)
            path.replace(package)
            retained[tag] = package
        except (OSError, ValueError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
            print(f'Historical IPA {tag} could not be mirrored ({type(error).__name__}); using its release URL.', flush=True)
    return retained


def legacy_package_redirects(releases, retained):
    return {mirrored_path(tag): f'https://github.com/{REPO}/releases/download/{tag}/{IPA}'
            for tag in releases if tag not in retained}


def rewrite_source(source, base, tag):
    result = copy.deepcopy(source)
    result.update(iconURL=base + '/icon.png', website=base,
                  description='按需构建的 ZhMusic 安装包与 SideStore 更新源。')
    app = current_app(result)
    app['iconURL'] = base + '/icon.png'
    app['versions'][0]['downloadURL'] = f'{base}/{mirrored_path(tag)}'
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
    request = urllib.request.Request(url, headers={
        'Cache-Control': 'no-cache', 'User-Agent': 'ZhMusicReleaseMirror/1.0'})
    digest, size = hashlib.sha256(), 0
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            while chunk := response.read(256 * 1024):
                size += len(chunk)
                if size > max_size:
                    raise ValueError('Deployed response exceeds expected size')
                digest.update(chunk)
    except urllib.error.HTTPError as error:
        if error.code == 403:
            # This request has no credentials; its public error page is safe to diagnose.
            snippet = ' '.join(error.read(1200).decode('utf-8', errors='replace').split())[:800]
            raise RuntimeError(f'Public download HTTP 403; server={error.headers.get("Server")}; '
                               f'mitigated={error.headers.get("cf-mitigated")}; response={snippet}') from None
        raise
    return digest.hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def verify_public_redirect(url, target):
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(url, timeout=30):
            raise ValueError('Historical IPA URL did not redirect')
    except urllib.error.HTTPError as error:
        if error.code != 302 or error.headers.get('Location') != target:
            raise ValueError(f'Historical IPA redirect did not match (HTTP {error.code})') from None


def verify_deployment(base, files, redirect=None):
    # Pages activation may lag behind the successful upload. Check actual bodies.
    for attempt in range(12):
        try:
            for relative, path in files.items():
                expected = hashlib.sha256(path.read_bytes()).hexdigest()
                if remote_digest(f'{base}/{relative}?verify={time.time_ns()}', path.stat().st_size) != expected:
                    raise ValueError(f'Deployed content differs: {relative}')
            if redirect:
                relative, target = redirect
                verify_public_redirect(f'{base}/{relative}', target)
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
        asset = package_asset(release)
        if asset is None:
            raise ValueError('Published IPA missing or ambiguous')
        archive = download_package(tag, root)
        verify_package(archive, version, asset)
        icon = (root / 'icon.png').read_bytes()
        if not icon.startswith(b'\x89PNG\r\n\x1a\n') or len(icon) > LIMIT:
            raise ValueError('Invalid published icon')
        base = project_base(account, token)
        output = root / 'site'
        releases = previous_release_assets(tag)
        package = output / mirrored_path(tag)
        package.parent.mkdir(parents=True)
        archive.replace(package)
        retained = retain_previous_packages(output, root, releases)
        redirects = legacy_package_redirects(releases, retained)
        if redirects:
            (output / '_redirects').write_text(''.join(f'/{path} {target} 302\n'
                for path, target in redirects.items()), encoding='utf-8')
        (output / 'icon.png').write_bytes(icon)
        rewritten = rewrite_source(source, base, tag)
        (output / 'source.json').write_text(json.dumps(rewritten, ensure_ascii=False, indent=2) + '\n')
        download = html.escape(current_app(rewritten)['versions'][0]['downloadURL'], quote=True)
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
        files = {'source.json': output / 'source.json', 'icon.png': output / 'icon.png',
                 mirrored_path(tag): package, **{mirrored_path(old): path for old, path in retained.items()}}
        verify_deployment(base, files, next(iter(redirects.items()), None))
        print(f'Cloudflare verified: {base}/source.json (version {version["version"]})')
        if summary := os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(summary, 'a') as handle:
                handle.write(f'\n### Cloudflare Pages\n\n- SideStore：{base}/source.json\n'
                             f'- 下载页：{base}\n- 版本：{version["version"]}\n'
                             f'- 已校验源、图标和最新版 IPA，另校验 {len(retained)} 个历史 IPA 镜像。\n'
                             f'- {len(redirects)} 个较旧的下载地址保留了对应的 GitHub 发布链接。\n')


if __name__ == '__main__':
    main()
