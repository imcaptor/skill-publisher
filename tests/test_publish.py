import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'publish.py'
spec = importlib.util.spec_from_file_location('publisher', SCRIPT)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'SKILL.md').write_text('---\nname: demo\ndescription: Demo\n---\n\nRun the demo.\n')
        (self.source / 'LICENSE').write_text('Test license for fixture only.')
        self.config = self.root / 'publish.json'
        self.data = {'source': 'source', 'output': 'dist', 'metadata': {'name': 'demo', 'version': '1.0.0', 'description': 'Demo skill', 'description_zh': '测试', 'description_en': 'Demo', 'author': 'Tester'}, 'include': ['**/*'], 'exclude': ['references/process-log.md'], 'license': 'LICENSE', 'platforms': {name: {'repository': 'owner/demo'} if name in ('github', 'gitee') else {} for name in p.PLATFORMS}}
        self.save()

    def tearDown(self):
        self.tmp.cleanup()

    def save(self):
        self.config.write_text(json.dumps(self.data))

    def cli(self, *args, env=None):
        return subprocess.run([sys.executable, str(SCRIPT)] + list(args), capture_output=True, text=True, env=env)

    def build(self):
        result = self.cli('build', '--config', str(self.config))
        self.assertEqual(result.returncode, 0, result.stderr)
        return Path(json.loads(result.stdout))

    def test_archive_secrets_nested_glob_deterministic_and_workbuddy(self):
        names = ['archives/poem.md', 'references/archives/poem.md', 'scripts/.env.local', '.git/config', 'references/private.key', 'credentials/password.txt', 'references/process-log.md', 'scripts/nested/run.py', 'references/guide.md', 'readme.txt']
        for name in names:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture ' + name)
        release = self.build()
        manifest = p.verify_release(release)
        allowed = {'SKILL.md', 'LICENSE', 'scripts/nested/run.py', 'references/guide.md', 'readme.txt'}
        self.assertEqual(set(manifest['files']), allowed)
        for name in ('skill.zip', 'source.zip', 'qianwen.zip'):
            with zipfile.ZipFile(release / name) as z:
                self.assertEqual(set(z.namelist()), allowed)
        with zipfile.ZipFile(release / 'workbuddy.zip') as z:
            self.assertEqual(set(z.namelist()), {'demo/' + name for name in allowed})
            front = z.read('demo/SKILL.md').decode()
            self.assertIn('description_zh: "测试"', front)
            self.assertIn('version: "1.0.0"', front)
        before = (release / 'skill.zip').read_bytes()
        self.assertEqual(self.build(), release)
        self.assertEqual(before, (release / 'skill.zip').read_bytes())
        self.assertNotIn(str(self.root), (release / 'manifest.json').read_text())

    def test_same_version_change_refused(self):
        self.build()
        (self.source / 'SKILL.md').write_text((self.source / 'SKILL.md').read_text() + 'Changed body.\n')
        result = self.cli('build', '--config', str(self.config))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('increment version', result.stderr)

    def test_skillhub_extensionless_license_preserves_content_and_other_packages(self):
        for license_name in ('LICENSE', 'legal/COPYING'):
            with self.subTest(license=license_name):
                license_file = self.source / license_name
                license_file.parent.mkdir(parents=True, exist_ok=True)
                content = b'License text\r\nCopyright \xc2\xa9 fixture\n'
                license_file.write_bytes(content)
                self.data['license'] = license_name
                self.data['metadata']['version'] = '1.0.' + str(len(license_name))
                self.save()
                before = {str(path.relative_to(self.source)): path.read_bytes() for path in self.source.rglob('*') if path.is_file()}
                release = self.build()
                with zipfile.ZipFile(release / 'skillhub.zip') as archive:
                    self.assertNotIn(license_name, archive.namelist())
                    self.assertEqual(archive.read(license_name + '.md'), content)
                for package in ('skill.zip', 'source.zip', 'qianwen.zip', 'workbuddy.zip'):
                    with zipfile.ZipFile(release / package) as archive:
                        prefix = 'demo/' if package == 'workbuddy.zip' else ''
                        self.assertEqual(archive.read(prefix + license_name), content)
                        self.assertNotIn(prefix + license_name + '.md', archive.namelist())
                manifest = p.verify_release(release)
                self.assertEqual(set(manifest['files']), set(before))
                self.assertEqual(manifest['license'], license_name)
                self.assertEqual(manifest['files'][license_name], p.digest(content))
                after = {str(path.relative_to(self.source)): path.read_bytes() for path in self.source.rglob('*') if path.is_file()}
                self.assertEqual(before, after)

    def test_skillhub_license_filename_collision(self):
        content = (self.source / 'LICENSE').read_bytes()
        (self.source / 'LICENSE.md').write_bytes(content + b'Different license')
        result = self.cli('build', '--config', str(self.config))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('LICENSE.md', result.stderr)
        self.assertFalse((self.root / 'dist/demo/1.0.0').exists())
        (self.source / 'LICENSE.md').write_bytes(content)
        release = self.build()
        with zipfile.ZipFile(release / 'skillhub.zip') as archive:
            self.assertNotIn('LICENSE', archive.namelist())
            self.assertEqual(archive.namelist().count('LICENSE.md'), 1)
            self.assertEqual(archive.read('LICENSE.md'), content)

    def test_symlink_and_output_escape_refused(self):
        (self.source / 'secret').symlink_to(self.root / 'outside')
        result = self.cli('build', '--config', str(self.config))
        self.assertNotEqual(result.returncode, 0)
        (self.source / 'secret').unlink()
        self.data['output'] = 'source/dist'
        self.save()
        self.assertNotEqual(self.cli('build', '--config', str(self.config)).returncode, 0)
        self.data['output'] = 'dist'
        self.data['include'] = ['../secret']
        self.save()
        self.assertNotEqual(self.cli('build', '--config', str(self.config)).returncode, 0)

    def test_source_deleted_preview_and_assisted_state(self):
        release = self.build()
        shutil.rmtree(self.source)
        result = self.cli('publish', '--release', str(release))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(not entry['execute'] for entry in json.loads(result.stdout)))
        self.assertEqual(p.publish(release, ['workbuddy'], True)[0]['mode'], 'assisted')
        self.assertEqual(p.read_json(release / 'status.json')['platforms']['workbuddy']['state'], 'prepared')
        self.assertEqual(p.record(release, 'workbuddy', 'submitted', 'https://open.workbuddy.cn/', 'Console confirms submission')['platforms']['workbuddy']['state'], 'submitted')
        p.publish(release, ['workbuddy'], True)
        self.assertEqual(p.read_json(release / 'status.json')['platforms']['workbuddy']['state'], 'submitted')

    def test_tamper_submission_or_manifest_refused(self):
        release = self.build()
        (release / 'qianwen-submission.json').write_text('{}')
        self.assertNotEqual(self.cli('publish', '--release', str(release)).returncode, 0)
        (release / 'qianwen-submission.json').write_text('')
        with self.assertRaises(p.PublishError):
            p.verify_release(release)

    def test_draft_license_guard(self):
        self.data['license'] = None
        self.save()
        release = self.build()
        self.assertEqual(self.cli('publish', '--release', str(release)).returncode, 0)
        self.assertNotEqual(self.cli('publish', '--release', str(release), '--execute').returncode, 0)

    def test_frontmatter_and_semver_validation(self):
        for invalid in ('1', 'v1.0.0', '01.0.0', '1.0.0-01'):
            self.data['metadata']['version'] = invalid
            self.save()
            self.assertNotEqual(self.cli('build', '--config', str(self.config)).returncode, 0)
        self.data['metadata']['version'] = '1.0.0-rc.1+build.2'
        self.save()
        self.build()
        for invalid in ('---\nname: demo\ndescription: Demo', '---\nname: other\ndescription: Demo\n---\n', '---\nname: demo\n---\n'):
            (self.source / 'SKILL.md').write_text(invalid)
            self.assertNotEqual(self.cli('build', '--config', str(self.config)).returncode, 0)

    def test_recursive_glob_matches_zero_middle_directories(self):
        self.assertTrue(p.matches('scripts/run.py', 'scripts/**/run.py'))
        self.assertTrue(p.matches('scripts/a/b/run.py', 'scripts/**/run.py'))
        self.assertFalse(p.matches('scripts/a/b/run.py', 'scripts/*.py'))

    def test_invalid_yaml_and_no_dependency_fallback(self):
        for content in (
            '---\nname: demo\ndescription: Demo: broken YAML\n---\n',
            '---\nname: demo\ndescription: Demo\nmetadata: [broken\n---\n',
            '---\nname: demo\ndescription: true\n---\n',
        ):
            (self.source / 'SKILL.md').write_text(content)
            self.assertNotEqual(self.cli('build', '--config', str(self.config)).returncode, 0)
            with patch.dict(sys.modules, {'yaml': None}):
                with self.assertRaises(p.PublishError):
                    p.validate_skill(content.encode(), 'demo')
        with patch.dict(sys.modules, {'yaml': None}):
            p.validate_skill(b'---\nname: demo\ndescription: "Demo"\ntags: ["writing"]\n---\n', 'demo')
            for scalar in ('yes', 'True', 'OFF', '42', '1.2', '2026-10-10', '-3', '.inf', '# only a comment', '- list item', '? mapping key', '] broken', ', broken'):
                content = ('---\nname: demo\ndescription: ' + scalar + '\n---\n').encode()
                with self.assertRaises(p.PublishError):
                    p.validate_skill(content, 'demo')
                quoted = ('---\nname: demo\ndescription: "' + scalar + '"\n---\n').encode()
                p.validate_skill(quoted, 'demo')

    def test_skillhub_nonobject_response_is_uncertain_and_channels_continue(self):
        self.data['platforms']['skillhub'] = {'license': 'MIT'}
        self.save()
        release = self.build()
        for response in ([], None):
            status = p.read_json(release / 'status.json')
            status['platforms']['skillhub'] = {'state': 'prepared'}
            p.atomic_json(release / 'status.json', status)
            with patch.object(p, 'credentials_env', return_value={'SKILLHUB_TOKEN': 'skh_fixture'}), patch.object(p.SkillHub, 'authenticate'), patch.object(p.SkillHub, 'submit', return_value=response):
                with self.assertRaises(p.PublishError):
                    p.publish(release, ['skillhub', 'workbuddy'], True)
            status = p.read_json(release / 'status.json')['platforms']
            self.assertEqual(status['skillhub']['state'], 'uncertain')
            self.assertEqual(status['workbuddy']['portal'], p.PORTALS['workbuddy'])
            with patch.object(p.SkillHub, 'request', return_value=response):
                with self.assertRaises(p.PublishError):
                    p.SkillHub('skh_fixture').authenticate()

    def test_skillhub_current_receipt_is_submitted_and_prevents_resubmission(self):
        self.data['platforms']['skillhub'] = {'license': 'MIT'}
        self.save()
        release = self.build()
        response = {'skillId': 274328, 'ok': True, 'reviewStatus': 'fixture-review',
                    'securityScanStatus': 'fixture-complete', 'contentAuditStatus': 'fixture-complete',
                    'iconAuditStatus': 'fixture-complete', 'iconAuditTaskStatus': 'fixture-complete'}
        with patch.object(p, 'credentials_env', return_value={'SKILLHUB_TOKEN': 'skh_fixture'}), patch.object(p.SkillHub, 'authenticate'), patch.object(p.SkillHub, 'submit', return_value=response) as submit:
            p.publish(release, ['skillhub'], True)
            status = p.read_json(release / 'status.json')['platforms']['skillhub']
            self.assertEqual(status['state'], 'submitted')
            self.assertEqual(status['receipt'], response)
            self.assertEqual(status['remote_status'], 'fixture-review')
            with self.assertRaises(p.PublishError):
                p.publish(release, ['skillhub'], True)
            self.assertEqual(submit.call_count, 1)

    def test_skillhub_explicit_legacy_published_receipt(self):
        self.data['platforms']['skillhub'] = {'license': 'MIT'}
        self.save()
        release = self.build()
        response = {'skillId': 'fixture-skill', 'status': 'published',
                    'publicUrl': 'https://skillhub.cn/skills/demo'}
        with patch.object(p, 'credentials_env', return_value={'SKILLHUB_TOKEN': 'skh_fixture'}), patch.object(p.SkillHub, 'authenticate'), patch.object(p.SkillHub, 'submit', return_value=response):
            p.publish(release, ['skillhub'], True)
        status = p.read_json(release / 'status.json')['platforms']['skillhub']
        self.assertEqual(status['state'], 'published')
        self.assertEqual(status['receipt'], response)

    def test_skillhub_incomplete_or_rejected_receipts_remain_uncertain(self):
        self.data['platforms']['skillhub'] = {'license': 'MIT'}
        self.save()
        release = self.build()
        responses = [
            {'skillId': 274328, 'ok': False, 'reviewStatus': 'fixture-review'},
            {'skillId': 274328, 'ok': False, 'status': 'published'},
            {'skillId': 274328, 'reviewStatus': 'fixture-review'},
            {'ok': True, 'reviewStatus': 'fixture-review'},
            {'skillId': 274328, 'ok': True},
            {'skillId': 274328, 'ok': True, 'reviewStatus': ''},
            {'skillId': 274328, 'ok': 'true', 'reviewStatus': 'fixture-review'},
            {'skillId': [], 'ok': True, 'reviewStatus': 'fixture-review'},
            {'skillId': True, 'ok': True, 'reviewStatus': 'fixture-review'},
            {'skillId': 274328, 'ok': True, 'reviewStatus': []},
            {'skillId': 274328, 'ok': True, 'reviewStatus': 'fixture-review', 'securityScanStatus': []},
        ]
        for response in responses:
            with self.subTest(response=response):
                status = p.read_json(release / 'status.json')
                status['platforms']['skillhub'] = {'state': 'prepared'}
                p.atomic_json(release / 'status.json', status)
                with patch.object(p, 'credentials_env', return_value={'SKILLHUB_TOKEN': 'skh_fixture'}), patch.object(p.SkillHub, 'authenticate'), patch.object(p.SkillHub, 'submit', return_value=response):
                    with self.assertRaises(p.PublishError):
                        p.publish(release, ['skillhub', 'workbuddy'], True)
                status = p.read_json(release / 'status.json')['platforms']
                self.assertEqual(status['skillhub']['state'], 'uncertain')
                self.assertEqual(status['workbuddy']['state'], 'prepared')

    def test_channels_continue_after_failure(self):
        release = self.build()
        with patch.object(p, 'publish_github', side_effect=p.PublishError('failed')):
            with self.assertRaises(p.PublishError):
                p.publish(release, ['github', 'workbuddy'], True)
        status = p.read_json(release / 'status.json')['platforms']
        self.assertEqual(status['github']['state'], 'failed')
        self.assertEqual(status['workbuddy']['portal'], p.PORTALS['workbuddy'])

    def test_skillhub_http_pending_receipt_and_uncertain_guard(self):
        self.data['platforms']['skillhub'] = {'license': 'MIT'}
        self.save()
        release = self.build()
        remote = {'posts': 0, 'body': b'', 'authorization': ''}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"user":{"id":"fixture-user"}}')
            def do_POST(self):
                remote['posts'] += 1
                remote['body'] = self.rfile.read(int(self.headers['Content-Length']))
                remote['authorization'] = self.headers['Authorization']
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"skillId":"fixture-skill","status":"pending_review","publicUrl":"https://skillhub.cn/skills/demo"}')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = p.SkillHub('skh_fixture')
        client.base = 'http://127.0.0.1:' + str(server.server_port)
        try:
            with patch.dict(os.environ, {'SKILLHUB_TOKEN': 'skh_fixture'}), patch.object(p, 'SkillHub', return_value=client):
                p.publish(release, ['skillhub'], True)
                status = p.read_json(release / 'status.json')['platforms']['skillhub']
                self.assertEqual(status['state'], 'submitted')
                self.assertEqual(status['remote_status'], 'pending_review')
                self.assertEqual(remote['authorization'], 'Bearer skh_fixture')
                self.assertIn(b'name="payload"', remote['body'])
                self.assertIn(b'name="files"; filename="SKILL.md"', remote['body'])
                self.assertNotIn(b'filename="skillhub.zip"', remote['body'])
                with self.assertRaises(p.PublishError):
                    p.publish(release, ['skillhub'], True)
                self.assertEqual(remote['posts'], 1)
                status_all = p.read_json(release / 'status.json')
                status_all['platforms']['skillhub'] = {'state': 'prepared'}
                p.atomic_json(release / 'status.json', status_all)
                with patch.object(client, 'submit', side_effect=p.PublishError('timeout')):
                    with self.assertRaises(p.PublishError):
                        p.publish(release, ['skillhub', 'workbuddy'], True)
                self.assertEqual(p.read_json(release / 'status.json')['platforms']['skillhub']['state'], 'uncertain')
                with self.assertRaises(p.PublishError):
                    p.publish(release, ['skillhub'], True)
                self.assertEqual(remote['posts'], 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def fake_gh(self):
        binary = self.root / 'bin'
        binary.mkdir()
        gh = binary / 'gh'
        gh.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args=sys.argv[1:]
root=Path(os.environ['FAKE_GH_STORE'])
root.mkdir(exist_ok=True)
with (root/'calls').open('a') as out: out.write(json.dumps(args)+'\\n')
if args[0]=='api':
 print(json.dumps([[{'tag_name':'v1.0.0'}]] if (root/'created').exists() else [[]]))
elif args[:2]==['release','create']:
 (root/'created').write_text('yes')
elif args[:2]==['release','view']:
 print(json.dumps({'url':'https://github.com/owner/demo/releases/tag/v1.0.0','assets':[{'name':f.name} for f in root.glob('*.zip')]+[{'name':f.name} for f in root.glob('*-submission.json')]}))
elif args[:2]==['release','upload']:
 source=Path(args[3])
 if os.environ.get('FAKE_FAIL_ONCE') and not (root/'failed').exists():
  (root/'failed').write_text('yes')
  print('sensitive credential should never leak',file=sys.stderr)
  sys.exit(1)
 (root/source.name).write_bytes(source.read_bytes())
elif args[:2]==['release','download']:
 name=args[args.index('--pattern')+1]
 directory=Path(args[args.index('--dir')+1])
 (directory/name).write_bytes((root/name).read_bytes())
else: sys.exit(2)
''')
        gh.chmod(0o755)
        store = self.root / 'remote'
        return dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'], FAKE_GH_STORE=str(store), FAKE_FAIL_ONCE='1'), store

    def test_github_real_cli_retry_and_remote_mismatch(self):
        release = self.build()
        env, store = self.fake_gh()
        args = ('publish', '--release', str(release), '--platform', 'github', '--execute')
        failed = self.cli(*args, env=env)
        self.assertNotEqual(failed.returncode, 0)
        self.assertNotIn('sensitive credential', failed.stderr)
        self.assertEqual(p.read_json(release / 'status.json')['platforms']['github']['state'], 'failed')
        retry = self.cli(*args, env=env)
        self.assertEqual(retry.returncode, 0, retry.stderr)
        calls = [json.loads(line) for line in (store / 'calls').read_text().splitlines()]
        self.assertEqual(sum(call[:2] == ['release', 'create'] for call in calls), 1)
        uploads = sum(call[:2] == ['release', 'upload'] for call in calls)
        success = self.cli(*args, env=env)
        self.assertEqual(success.returncode, 0, success.stderr)
        calls2 = [json.loads(line) for line in (store / 'calls').read_text().splitlines()]
        self.assertEqual(sum(call[:2] == ['release', 'upload'] for call in calls2), uploads)
        (store / 'skill.zip').write_bytes(b'wrong')
        self.assertNotEqual(self.cli(*args, env=env).returncode, 0)

    def test_gitee_http_create_upload_verify_and_retry(self):
        release = self.build()
        manifest = p.verify_release(release)
        remote = {'created': False, 'assets': {}, 'uploads': 0, 'requests': []}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def respond(self, value):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(value).encode())
            def do_GET(self):
                remote['requests'].append(self.path)
                route = self.path.split('?')[0]
                if route.endswith('/releases'):
                    self.respond([{'id': 17, 'tag_name': 'v1.0.0'}] if remote['created'] else [])
                elif route.endswith('/releases/17'):
                    self.respond({'id': 17, 'assets': [{'name': name, 'browser_download_url': 'https://gitee.com/assets/' + name} for name in remote['assets']]})
                else:
                    self.respond({'default_branch': 'main'})
            def do_POST(self):
                content = self.rfile.read(int(self.headers['Content-Length']))
                if self.path.endswith('/attach_files'):
                    boundary = self.headers['Content-Type'].split('boundary=')[1].encode()
                    part = content.split(b'--' + boundary)[2]
                    header, payload = part.split(b'\r\n\r\n', 1)
                    import re
                    name = re.search(br'filename="([^"]+)"', header).group(1).decode()
                    remote['assets'][name] = payload[:-2]
                    remote['uploads'] += 1
                    self.respond({'name': name})
                else:
                    self.assert_create = content
                    remote['created'] = True
                    self.respond({'id': 17, 'tag_name': 'v1.0.0'})
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = p.Gitee('test-token')
        client.base = 'http://127.0.0.1:' + str(server.server_port)
        try:
            with patch.dict(os.environ, {'GITEE_TOKEN': 'test-token'}), patch.object(p, 'Gitee', return_value=client), patch.object(client, 'download', side_effect=lambda url: remote['assets'][url.rsplit('/', 1)[1]]):
                p.publish(release, ['gitee'], True)
                uploads = remote['uploads']
                p.publish(release, ['gitee'], True)
                self.assertEqual(remote['uploads'], uploads)
                self.assertEqual(uploads, len(manifest['artifacts']))
                remote['assets']['skill.zip'] = b'bad'
                with self.assertRaises(p.PublishError):
                    p.publish(release, ['gitee'], True)
                self.assertEqual(remote['uploads'], uploads)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

if __name__ == '__main__':
    unittest.main()
