#!/usr/bin/env python3
import contextlib
import copy
import datetime
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('janitor', Path(__file__).with_name('cleanup-stale.py'))
janitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(janitor)
NOW = datetime.datetime(2026, 10, 29, 14, 0, tzinfo=datetime.timezone.utc)


def repo():
    name = 'zhmusic-tmp-build-20261029090000-abcdef123456'
    return {'id': 123, 'name': name, 'full_name': 'zz8392570-ops/' + name,
            'owner': {'login': 'zz8392570-ops'}, 'private': False,
            'description': janitor.MARKER_PREFIX + name[len(janitor.PREFIX):],
            'created_at': '2026-10-29T09:00:03Z'}


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))

    def invoke(self, record, run_states=None, current=None, change_after=None, fail_delete=False):
        events = []
        reads = 0
        def gh(*args):
            nonlocal reads
            events.append(args)
            if args == ('api', 'user'):
                return json.dumps({'login': janitor.OWNER})
            if args[:3] == ('api', '--paginate', 'user/repos?affiliation=owner&per_page=100'):
                return json.dumps([record])
            if args[:3] == ('api', '--method', 'DELETE'):
                if fail_delete:
                    raise subprocess.CalledProcessError(1, args)
                return ''
            if '/actions/runs?' in args[-1]:
                return json.dumps(run_states if run_states is not None else {'total_count': 0, 'workflow_runs': []})
            reads += 1
            return json.dumps(change_after if reads > 1 and change_after is not None else (current or record))
        with patch.object(janitor, 'gh', side_effect=gh):
            if fail_delete:
                with self.assertRaisesRegex(RuntimeError, 'Cleanup incomplete'):
                    janitor.cleanup(NOW)
            else:
                janitor.cleanup(NOW)
        return [event for event in events if event[:3] == ('api', '--method', 'DELETE')]

    def test_old_marked_inactive_repo_is_deleted(self):
        self.assertEqual(len(self.invoke(repo())), 1)

    def test_active_and_unknown_runs_are_never_deleted(self):
        for status in ['queued', 'in_progress', 'requested', 'waiting', 'pending', None]:
            with self.subTest(status=status):
                self.assertFalse(self.invoke(repo(), {'total_count': 1, 'workflow_runs': [{'status': status}]}))

    def test_incomplete_workflow_results_fail_closed(self):
        for response in [{}, {'total_count': 101, 'workflow_runs': []}, {'total_count': 1, 'workflow_runs': []}]:
            with self.subTest(response=response):
                with self.assertRaisesRegex(RuntimeError, 'Cleanup incomplete'):
                    self.invoke(repo(), response)

    def test_unmarked_private_young_wrong_owner_and_source_repos_are_never_deleted(self):
        cases = []
        for changes in [
            {'description': 'unrelated'}, {'private': True}, {'owner': {'login': 'other'}},
            {'name': 'Zhmusic', 'full_name': 'zz8392570-ops/Zhmusic'},
            {'created_at': '2026-10-29T13:30:00Z'},
        ]:
            value = repo()
            value.update(changes)
            cases.append(value)
        for case in cases:
            with self.subTest(case=case):
                self.assertFalse(self.invoke(case))

    def test_changed_repository_identity_is_never_deleted(self):
        other = copy.deepcopy(repo())
        other['id'] = 999
        self.assertFalse(self.invoke(repo(), change_after=other))

    def test_delete_failure_is_reported(self):
        self.invoke(repo(), fail_delete=True)

    def test_wrong_token_owner_stops_before_listing_or_deleting(self):
        with patch.object(janitor, 'gh', return_value=json.dumps({'login': 'other'})) as gh:
            with self.assertRaisesRegex(RuntimeError, 'Token owner'):
                janitor.cleanup(NOW)
        self.assertEqual(gh.call_count, 1)

    def test_paginated_json_arrays_are_all_read(self):
        with patch.object(janitor, 'gh', return_value='[{"id":1}]\n[{"id":2}]\n'):
            self.assertEqual(janitor.owned_repositories(), [{'id': 1}, {'id': 2}])

    def test_invalid_listing_is_never_treated_as_success(self):
        with patch.object(janitor, 'gh', return_value='{"message":"denied"}'):
            with self.assertRaises(ValueError):
                janitor.owned_repositories()


if __name__ == '__main__':
    unittest.main()
