#!/usr/bin/env python3
import copy
import hashlib
import importlib.util
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
BASE = 'https://zhmusic-download.pages.dev'


class Checks(unittest.TestCase):
    def setUp(self):
        self.source = {'identifier': 'keep-original-identifier', 'iconURL': 'old-icon', 'apps': [{
            'bundleIdentifier': 'com.music.player.gyc', 'iconURL': 'old-icon', 'versions': [{
                'version': '2.0.1002', 'buildVersion': '1002.1', 'size': 200,
                'downloadURL': f'https://github.com/{mirror.REPO}/releases/download/{TAG}/{mirror.IPA}'}]}]}

    def test_valid_metadata(self):
        self.assertEqual(mirror.package_metadata(self.source)[0], TAG)

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
                    'CFBundleIdentifier': 'com.music.player.gyc',
                    'CFBundleShortVersionString': '2.0.1002', 'CFBundleVersion': '1002.1'}))
            version = dict(self.source['apps'][0]['versions'][0], size=path.stat().st_size)
            asset = {'size': version['size'], 'digest': 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()}
            mirror.verify_package(path, version, asset)
            with self.assertRaises(ValueError):
                mirror.verify_package(path, dict(version, buildVersion='1001.1'), asset)
            with self.assertRaises(ValueError):
                mirror.verify_package(path, version, dict(asset, digest='sha256:bad'))

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
