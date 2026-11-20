#!/usr/bin/env python3
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

spec = importlib.util.spec_from_file_location('mirror', Path(__file__).with_name('cloudflare-pages.py'))
mirror = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mirror)
TAG = 'build-20261029100742-901e70cbb3af'
PAST_TAG = 'build-20261028100742-901e70cbb3af'
OLDER_TAG = 'build-20261027100742-901e70cbb3af'
BASE = 'https://zhmusic-download.pages.dev'


class Checks(unittest.TestCase):
    def setUp(self):
        self.source = {'identifier': 'keep-original-identifier', 'iconURL': 'old-icon', 'apps': [{
            'bundleIdentifier': mirror.APP_ID, 'iconURL': 'old-icon', 'versions': [{
                'version': '2.0.1002', 'buildVersion': '1002.1', 'size': 200,
                'downloadURL': f'https://github.com/{mirror.REPO}/releases/download/{TAG}/{mirror.IPA}'}]}]}

    def test_valid_metadata(self):
        self.assertEqual(mirror.package_metadata(self.source)[0], TAG)

    def test_legacy_entry_is_preserved_without_redirecting_it_to_new_package(self):
        legacy = {'bundleIdentifier': 'com.music.player.gyc', 'versions': [{'downloadURL': 'https://example.test/old.ipa'}]}
        self.source['apps'].insert(0, legacy)
        self.assertEqual(mirror.package_metadata(self.source)[0], TAG)
        result = mirror.rewrite_source(self.source, BASE, TAG)
        self.assertEqual(result['apps'][0], legacy)
        self.assertEqual(result['apps'][1]['versions'][0]['downloadURL'], f'{BASE}/releases/{TAG}/{mirror.IPA}')

    def test_missing_or_duplicate_new_identity_is_rejected(self):
        app = self.source['apps'][0]
        self.source['apps'] = []
        with self.assertRaises(ValueError):
            mirror.package_metadata(self.source)
        self.source['apps'] = [app, copy.deepcopy(app)]
        with self.assertRaises(ValueError):
            mirror.package_metadata(self.source)

    def test_reject_untrusted_downloads(self):
        version = self.source['apps'][0]['versions'][0]
        for url in ['https://evil.invalid/x.ipa', version['downloadURL'] + '?extra=1',
                    version['downloadURL'].replace(mirror.REPO, 'other/repo')]:
            version['downloadURL'] = url
            with self.assertRaises(ValueError):
                mirror.package_metadata(self.source)

    def test_reject_size_overflow(self):
        for size in [0, -1, True, mirror.LIMIT + 1]:
            self.source['apps'][0]['versions'][0]['size'] = size
            with self.assertRaises(ValueError):
                mirror.package_metadata(self.source)

    def test_no_missing_versions(self):
        self.source['apps'][0]['versions'].append(copy.deepcopy(self.source['apps'][0]['versions'][0]))
        with self.assertRaises(ValueError):
            mirror.package_metadata(self.source)

    def test_rewrite_preserves_identity_and_original(self):
        original = copy.deepcopy(self.source)
        result = mirror.rewrite_source(self.source, BASE, TAG)
        self.assertEqual(self.source, original)
        self.assertEqual(result['identifier'], original['identifier'])
        self.assertEqual(result['apps'][0]['versions'][0]['downloadURL'], f'{BASE}/releases/{TAG}/{mirror.IPA}')
        self.assertEqual(result['iconURL'], BASE + '/icon.png')
        self.assertEqual(result['apps'][0]['iconURL'], BASE + '/icon.png')

    def test_package_integrity_and_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / mirror.IPA
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('Payload/ZhMusic.app/Info.plist', plistlib.dumps({
                    'CFBundleIdentifier': mirror.APP_ID,
                    'CFBundleShortVersionString': '2.0.1002', 'CFBundleVersion': '1002.1'}))
            version = dict(self.source['apps'][0]['versions'][0], size=path.stat().st_size)
            asset = {'size': version['size'], 'digest': 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()}
            mirror.verify_package(path, version, asset)
            with self.assertRaises(ValueError):
                mirror.verify_package(path, dict(version, buildVersion='1001.1'), asset)
            with self.assertRaises(ValueError):
                mirror.verify_package(path, version, dict(asset, digest='sha256:bad'))

    def test_previous_assets_require_a_published_build_with_an_ipa(self):
        asset = {'name': mirror.IPA, 'size': 123, 'digest': 'sha256:fixture'}
        releases = [
            {'tag_name': TAG, 'assets': [asset]},
            {'tag_name': PAST_TAG, 'assets': [asset]},
            {'tag_name': OLDER_TAG, 'assets': [asset], 'draft': True},
            {'tag_name': 'sidestore', 'assets': [asset]},
            {'tag_name': 'build/../../foo', 'assets': [asset]},
        ]
        with patch.object(mirror, 'command', return_value='\n'.join(map(json.dumps, releases))):
            self.assertEqual(mirror.previous_release_assets(TAG), {PAST_TAG: asset})

    def test_previous_ipa_keeps_its_exact_link_and_old_entries_redirect_to_their_own_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'site'
            existing = root / mirror.IPA
            with zipfile.ZipFile(existing, 'w') as archive:
                archive.writestr('Payload/ZhMusic.app/Info.plist', plistlib.dumps({
                    'CFBundleIdentifier': mirror.APP_ID,
                    'CFBundleShortVersionString': '2.0.1001', 'CFBundleVersion': '1001.1'}))
            blob = existing.read_bytes()
            asset = {'size': len(blob), 'digest': 'sha256:' + hashlib.sha256(blob).hexdigest()}
            releases = {PAST_TAG: asset, OLDER_TAG: asset}
            with patch.object(mirror, 'PREVIOUS_MIRRORS', 1), patch.object(mirror, 'download_package', return_value=existing):
                retained = mirror.retain_previous_packages(output, root, releases)
            self.assertEqual(retained[PAST_TAG].relative_to(output).as_posix(), mirror.mirrored_path(PAST_TAG))
            self.assertEqual(retained[PAST_TAG].read_bytes(), blob)
            self.assertEqual(mirror.legacy_package_redirects(releases, retained), {
                mirror.mirrored_path(OLDER_TAG): f'https://github.com/{mirror.REPO}/releases/download/{OLDER_TAG}/{mirror.IPA}'})
            self.assertEqual(len(mirror.current_app(mirror.rewrite_source(self.source, BASE, TAG))['versions']), 1)

    def test_public_redirect_requires_a_match_to_the_release_url(self):
        url = f'{BASE}/{mirror.mirrored_path(OLDER_TAG)}'
        target = f'https://github.com/{mirror.REPO}/releases/download/{OLDER_TAG}/{mirror.IPA}'
        with patch.object(mirror.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = urllib.error.HTTPError(url, 302, 'Found', {'Location': target}, None)
            mirror.verify_public_redirect(url, target)
            with self.assertRaises(ValueError):
                mirror.verify_public_redirect(url, target + '.wrong')

    def test_existing_project_not_recreated(self):
        with patch.object(mirror, 'cf_api', return_value={'production_branch': 'main', 'subdomain': 'example.pages.dev'}) as api:
            self.assertEqual(mirror.project_base('account', 'token'), 'https://example.pages.dev')
            self.assertEqual(api.call_count, 1)

    def test_create_missing_project_only(self):
        with patch.object(mirror, 'cf_api', side_effect=[None, {'production_branch': 'main', 'subdomain': 'example.pages.dev'}]) as api:
            mirror.project_base('account', 'token')
            self.assertEqual(api.call_args.args[2], 'POST')

    def test_reject_wrong_production_branch(self):
        with patch.object(mirror, 'cf_api', return_value={'production_branch': 'other', 'subdomain': 'example.pages.dev'}):
            with self.assertRaises(ValueError):
                mirror.project_base('account', 'token')

    def test_retry_public_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.json'
            path.write_bytes(b'new source')
            expected = hashlib.sha256(path.read_bytes()).hexdigest()
            with patch.object(mirror, 'remote_digest', side_effect=['stale', expected]) as read, patch.object(mirror.time, 'sleep'):
                mirror.verify_deployment(BASE, {'source.json': path})
                self.assertEqual(read.call_count, 2)

    def test_verification_cannot_report_false_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.json'
            path.write_bytes(b'new source')
            with patch.object(mirror, 'remote_digest', side_effect=urllib.error.URLError('offline')), patch.object(mirror.time, 'sleep'):
                with self.assertRaises(RuntimeError):
                    mirror.verify_deployment(BASE, {'source.json': path})


unittest.main()
