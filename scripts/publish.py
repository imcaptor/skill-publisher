#!/usr/bin/env python3
"""Build immutable skill releases and publish their verified assets."""
import argparse
import fnmatch
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from contextlib import contextmanager

PLATFORMS = ('github', 'gitee', 'workbuddy', 'qianwen', 'skillhub')
PORTALS = {'workbuddy': 'https://open.workbuddy.cn/', 'qianwen': 'https://www.qianwenai.com/hub/skills'}

class PublishError(Exception):
    pass

def digest(data):
    return hashlib.sha256(data).hexdigest()

def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()

def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise PublishError('Cannot read valid JSON: ' + Path(path).name) from exc

def atomic_json(path, value):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

@contextmanager
def lock(directory):
    path = Path(directory) / '.publisher.lock'
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise PublishError('Another operation holds the release lock. Remove a stale lock only after checking its process.') from exc
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        path.unlink()

def safe_label(value, field):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', value) or value in ('.', '..'):
        raise PublishError('Invalid ' + field)
    return value

def safe_relative(value):
    if not isinstance(value, str) or '\\' in value or any(ord(char) < 32 for char in value) or '"' in value:
        raise PublishError('Paths must be relative POSIX paths')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise PublishError('Paths must stay inside their root')
    return value

def blocked(relative):
    for part in PurePosixPath(relative).parts:
        lower = part.lower()
        if lower in ('archives', 'archive', '.git', '__pycache__', '.cache', 'node_modules', 'credentials', '.credentials', 'secrets', '.ssh', '.aws', 'keys', 'key'):
            return True
        if lower.startswith('.env') or lower.endswith(('.pem', '.key', '.p12', '.pfx')) or lower in ('id_rsa', 'id_ed25519', 'credentials.json', '.netrc', '.npmrc'):
            return True
    return False

def matches(path, pattern):
    parts, patterns = PurePosixPath(path).parts, PurePosixPath(pattern).parts
    def compare(path_parts, pattern_parts):
        if not pattern_parts:
            return not path_parts
        if pattern_parts[0] == '**':
            return compare(path_parts, pattern_parts[1:]) or bool(path_parts and compare(path_parts[1:], pattern_parts))
        return bool(path_parts and fnmatch.fnmatchcase(path_parts[0], pattern_parts[0]) and compare(path_parts[1:], pattern_parts[1:]))
    return compare(parts, patterns)

def load_config(path):
    path = Path(path).resolve()
    config = read_json(path)
    if not isinstance(config, dict):
        raise PublishError('Config must be an object')
    metadata = config.get('metadata', {})
    fields = {'name', 'version', 'description', 'description_zh', 'description_en', 'author'}
    if not isinstance(metadata, dict) or set(metadata) - fields:
        raise PublishError('metadata accepts only name, version, description, description_zh, description_en and author')
    safe_label(metadata.get('name'), 'name')
    if not isinstance(metadata.get('version'), str) or len(metadata['version']) > 100:
        raise PublishError('Invalid version')
    if not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?', metadata['version']):
        raise PublishError('version must use SemVer, for example 1.2.0')
    for key in ('description', 'description_zh', 'description_en', 'author'):
        if not isinstance(metadata.get(key), str) or not metadata[key].strip():
            raise PublishError('metadata.' + key + ' is required')
    includes = config.get('include', ['SKILL.md', 'scripts/**', 'references/**', 'README.md', 'LICENSE'])
    excludes = config.get('exclude', [])
    if not isinstance(includes, list) or not includes or not isinstance(excludes, list):
        raise PublishError('include and exclude must be arrays of relative patterns')
    for pattern in includes + excludes:
        safe_relative(pattern)
    source = (path.parent / config['source']).resolve()
    output = (path.parent / config.get('output', 'dist')).resolve()
    if output == source or source in output.parents:
        raise PublishError('Output must be outside source')
    platforms = config.get('platforms', {})
    if not isinstance(platforms, dict) or set(platforms) - set(PLATFORMS):
        raise PublishError('Unknown platform configuration')
    return config, source, output, includes, excludes

def collect(source, includes, excludes):
    if not source.is_dir():
        raise PublishError('Source directory does not exist')
    files = {}
    for root, dirs, names in os.walk(source, followlinks=False):
        for name in list(dirs) + names:
            path = Path(root) / name
            relative = path.relative_to(source).as_posix()
            if path.is_symlink():
                raise PublishError('Symlinks are refused: ' + relative)
        dirs[:] = sorted(d for d in dirs if not blocked((Path(root) / d).relative_to(source).as_posix()) and not any(matches((Path(root) / d).relative_to(source).as_posix(), p) for p in excludes))
        for name in sorted(names):
            path = Path(root) / name
            relative = path.relative_to(source).as_posix()
            if blocked(relative) or not any(matches(relative, p) for p in includes) or any(matches(relative, p) for p in excludes):
                continue
            if not path.is_file() or source not in path.resolve().parents:
                raise PublishError('Unsafe source file: ' + relative)
            files[relative] = path.read_bytes()
    if 'SKILL.md' not in files:
        raise PublishError('Allowlist must include SKILL.md')
    return files

def make_zip(files, prefix=''):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(prefix + name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    return buffer.getvalue()

def frontmatter(content):
    try:
        text = content.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise PublishError('SKILL.md must be UTF-8') from exc
    lines = text.splitlines()
    if not lines or lines[0] != '---':
        raise PublishError('SKILL.md requires YAML frontmatter')
    try:
        end = lines.index('---', 1)
    except ValueError as exc:
        raise PublishError('SKILL.md frontmatter is not closed') from exc
    return lines[1:end], '\n'.join(lines[end + 1:]).lstrip('\n') + '\n'

def validate_skill(content, expected_name):
    lines, _ = frontmatter(content)
    required = {}
    for line in lines:
        match = re.match(r'^(name|description):\s*(.*)$', line)
        if not match:
            continue
        key, value = match.groups()
        if key in required:
            raise PublishError('Duplicate SKILL.md field: ' + key)
        try:
            if value.startswith('"'):
                value = json.loads(value)
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1].replace("''", "'")
            elif not value or value[0] in '|>{[&*!':
                raise ValueError()
        except ValueError as exc:
            raise PublishError('SKILL.md name/description must be nonempty quoted or plain scalars') from exc
        if not isinstance(value, str) or not value.strip():
            raise PublishError('SKILL.md ' + key + ' is required')
        required[key] = value
    if set(required) != {'name', 'description'}:
        raise PublishError('SKILL.md name and description are required')
    if required['name'] != expected_name:
        raise PublishError('metadata.name must match SKILL.md name')

def replace_frontmatter(content, metadata):
    old, body = frontmatter(content)
    keys = tuple(metadata)
    kept, skipping = [], False
    for line in old:
        match = re.match(r'^([A-Za-z_][A-Za-z0-9_-]*):', line)
        if match:
            skipping = match.group(1) in keys
        if not skipping:
            kept.append(line)
    front = [key + ': ' + json.dumps(metadata[key], ensure_ascii=False) for key in keys]
    return ('---\n' + '\n'.join(front + kept) + '\n---\n\n' + body).encode()

def public_platforms(config):
    result = {}
    for name, settings in config.get('platforms', {}).items():
        if not isinstance(settings, dict):
            raise PublishError('Platform settings must be objects')
        allowed = {'slug', 'displayName', 'summary', 'license', 'homepage', 'tags', 'changelog', 'enabled'} if name == 'skillhub' else {'repository', 'target_commitish', 'enabled'}
        if set(settings) - allowed:
            raise PublishError('Unsupported platform settings; credentials belong in environment')
        if settings.get('enabled', True):
            result[name] = settings
    return result

def build(config_path):
    config, source, output, includes, excludes = load_config(config_path)
    files = collect(source, includes, excludes)
    metadata = config['metadata']
    validate_skill(files['SKILL.md'], metadata['name'])
    license_path = config.get('license')
    if license_path is not None:
        safe_relative(license_path)
        if license_path not in files or not files[license_path].strip():
            raise PublishError('Configured license must be a nonempty allowlisted file')
    platforms = public_platforms(config)
    common = make_zip(files)
    work_files = dict(files)
    work_files['SKILL.md'] = replace_frontmatter(files['SKILL.md'], metadata)
    assets = {'skill.zip': common, 'source.zip': common, 'workbuddy.zip': make_zip(work_files, metadata['name'] + '/'), 'qianwen.zip': common}
    if 'skillhub' in platforms:
        settings = platforms['skillhub']
        payload = skillhub_metadata(metadata, settings)
        hub_files = dict(files)
        hub_files['SKILL.md'] = replace_frontmatter(files['SKILL.md'], {key: value for key, value in payload.items() if key != 'changelog'})
        assets['skillhub.zip'] = make_zip(hub_files)
        assets['skillhub-submission.json'] = json_bytes(payload)
    if len(common) > 10 * 1024 * 1024:
        raise PublishError('Qianwen ZIP exceeds conservative 10 MiB limit')
    for platform in ('workbuddy', 'qianwen'):
        assets[platform + '-submission.json'] = json_bytes({'platform': platform, 'metadata': metadata, 'portal': PORTALS[platform], 'package': platform + '.zip', 'steps': ['Sign in and complete required account verification.', 'Create a skill and upload this ZIP.', 'Fill metadata and test in the platform.', 'Submit for review; record submitted or published with evidence.'], 'capability': 'assisted'})
    manifest = {'schema': 1, 'metadata': metadata, 'license': license_path, 'platforms': platforms, 'files': {name: digest(content) for name, content in sorted(files.items())}, 'artifacts': {name: digest(content) for name, content in sorted(assets.items())}}
    encoded = json_bytes(manifest)
    release = output / metadata['name'] / metadata['version']
    release.parent.mkdir(parents=True, exist_ok=True)
    with lock(release.parent):
        if release.exists():
            verify_release(release)
            if (release / 'manifest.json').read_bytes() != encoded:
                raise PublishError('This version already contains different content; increment version')
            return release
        temp = Path(tempfile.mkdtemp(prefix='.build-', dir=release.parent))
        try:
            for name, content in assets.items():
                (temp / name).write_bytes(content)
            (temp / 'manifest.json').write_bytes(encoded)
            (temp / 'manifest.sha256').write_text(digest(encoded) + '\n')
            atomic_json(temp / 'status.json', {'platforms': {p: {'state': 'prepared'} for p in platforms}})
            os.rename(temp, release)
        finally:
            if temp.exists():
                shutil.rmtree(temp)
    return release

def verify_release(release):
    release = Path(release).resolve()
    if not release.is_dir():
        raise PublishError('Release directory does not exist')
    for name in ('manifest.json', 'manifest.sha256', 'status.json'):
        if (release / name).is_symlink():
            raise PublishError('Release symlinks are refused')
    raw = (release / 'manifest.json').read_bytes()
    if digest(raw) != (release / 'manifest.sha256').read_text().strip():
        raise PublishError('Manifest checksum mismatch')
    manifest = read_json(release / 'manifest.json')
    for name, expected in manifest['artifacts'].items():
        safe_relative(name)
        path = release / name
        if path.is_symlink() or not path.is_file() or digest(path.read_bytes()) != expected:
            raise PublishError('Artifact checksum mismatch: ' + name)
    return manifest

def repository(settings):
    repo = settings.get('repository')
    if not isinstance(repo, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', repo):
        raise PublishError('Configure repository as owner/repo before publishing')
    return repo

def credentials_env():
    environment = dict(os.environ)
    path = Path.home() / '.config' / 'skill-publisher' / 'credentials.json'
    if not path.exists():
        return environment
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise PublishError('Private credentials file must be a regular file with mode 0600')
    credentials = read_json(path)
    if not isinstance(credentials, dict) or set(credentials) - {'GH_TOKEN', 'GITHUB_TOKEN', 'GITEE_TOKEN', 'SKILLHUB_API_KEY', 'SKILLHUB_TOKEN'}:
        raise PublishError('Private credentials file contains unsupported fields')
    for key, value in credentials.items():
        if not isinstance(value, str):
            raise PublishError('Private credential values must be strings')
        if key not in environment:
            environment[key] = value
    return environment

def run_gh(args):
    try:
        result = subprocess.run(['gh'] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, env=credentials_env())
    except FileNotFoundError as exc:
        raise PublishError('Install gh and sign in with gh auth login') from exc
    if result.returncode:
        # gh output may contain tokens or user-sensitive response bodies.
        raise PublishError('GitHub CLI request failed; check authentication, repository and release permissions')
    return result.stdout

def gh_json(args):
    try:
        return json.loads(run_gh(args))
    except ValueError as exc:
        raise PublishError('GitHub CLI returned invalid JSON') from exc

def publish_github(release, manifest, settings):
    repo = repository(settings)
    tag = 'v' + manifest['metadata']['version']
    releases = gh_json(['api', '--paginate', '--slurp', 'repos/' + repo + '/releases?per_page=100'])
    items = [item for page in releases for item in page] if releases and isinstance(releases[0], list) else releases
    found = next((item for item in items if item['tag_name'] == tag), None)
    if found is None:
        args = ['release', 'create', tag, '--repo', repo, '--title', manifest['metadata']['name'] + ' ' + tag, '--notes', manifest['metadata']['description']]
        target = settings.get('target_commitish')
        if target:
            args += ['--target', target]
        run_gh(args)
    info = gh_json(['release', 'view', tag, '--repo', repo, '--json', 'assets,url'])
    assets = {asset['name']: asset for asset in info['assets']}
    for name, expected in manifest['artifacts'].items():
        if name in assets:
            with tempfile.TemporaryDirectory() as directory:
                run_gh(['release', 'download', tag, '--repo', repo, '--pattern', name, '--dir', directory])
                downloaded = Path(directory) / name
                if not downloaded.is_file() or digest(downloaded.read_bytes()) != expected:
                    raise PublishError('Remote GitHub asset differs: ' + name)
        else:
            run_gh(['release', 'upload', tag, str(release / name), '--repo', repo])
            with tempfile.TemporaryDirectory() as directory:
                run_gh(['release', 'download', tag, '--repo', repo, '--pattern', name, '--dir', directory])
                if digest((Path(directory) / name).read_bytes()) != expected:
                    raise PublishError('Uploaded GitHub asset checksum differs: ' + name)
    return info['url']

class Gitee:
    def __init__(self, token):
        self.token = token
        self.base = 'https://gitee.com/api/v5'

    def request(self, path, method='GET', data=None, multipart=None):
        url = self.base + path
        headers = {}
        if method == 'GET':
            url += ('&' if '?' in url else '?') + urllib.parse.urlencode({'access_token': self.token})
            body = None
        elif multipart is not None:
            boundary = 'skillpublisher' + hashlib.sha256(multipart[1]).hexdigest()[:24]
            filename, content = multipart
            body = ('--' + boundary + '\r\nContent-Disposition: form-data; name="access_token"\r\n\r\n' + self.token + '\r\n--' + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="' + filename + '"\r\nContent-Type: application/octet-stream\r\n\r\n').encode() + content + ('\r\n--' + boundary + '--\r\n').encode()
            headers['Content-Type'] = 'multipart/form-data; boundary=' + boundary
        else:
            body = urllib.parse.urlencode(dict(data or {}, access_token=self.token)).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        try:
            with urllib.request.urlopen(urllib.request.Request(url, body, headers, method=method), timeout=60) as response:
                return json.load(response)
        except (urllib.error.URLError, ValueError) as exc:
            raise PublishError('Gitee request failed; check credentials, repository and release permissions') from exc

    def download(self, url):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise PublishError('Gitee asset download must use HTTPS')
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return response.read()
        except urllib.error.URLError as exc:
            raise PublishError('Cannot verify Gitee remote asset; refusing to overwrite') from exc

def publish_gitee(release, manifest, settings):
    repo = repository(settings)
    token = credentials_env().get('GITEE_TOKEN')
    if not token:
        raise PublishError('Set GITEE_TOKEN in environment')
    client = Gitee(token)
    path = '/repos/' + repo + '/releases'
    tag = 'v' + manifest['metadata']['version']
    found, page = None, 1
    while True:
        items = client.request(path + '?per_page=100&page=' + str(page))
        found = next((item for item in items if item['tag_name'] == tag), None)
        if found or len(items) < 100:
            break
        page += 1
    if found is None:
        target = settings.get('target_commitish')
        if not target:
            target = client.request('/repos/' + repo)['default_branch']
        found = client.request(path, 'POST', {'tag_name': tag, 'target_commitish': target, 'name': manifest['metadata']['name'] + ' ' + tag, 'body': manifest['metadata']['description']})
    release_path = path + '/' + str(found['id'])
    details = client.request(release_path)
    existing = {asset['name']: asset for asset in details.get('assets', [])}
    for name, expected in manifest['artifacts'].items():
        asset = existing.get(name)
        if asset is None:
            uploaded = client.request(release_path + '/attach_files', 'POST', multipart=(name, (release / name).read_bytes()))
            details = client.request(release_path)
            asset = next((entry for entry in details.get('assets', []) if entry.get('name') == name), None)
            if asset is None and isinstance(uploaded, dict) and uploaded.get('name') == name:
                asset = uploaded
        url = (asset or {}).get('browser_download_url')
        if not url or digest(client.download(url)) != expected:
            raise PublishError('Cannot confirm identical Gitee asset: ' + name)
    return 'https://gitee.com/' + repo + '/releases/' + tag

def skillhub_metadata(metadata, settings):
    payload = {'slug': settings.get('slug', metadata['name']), 'version': metadata['version'], 'displayName': settings.get('displayName', metadata['name']), 'summary': settings.get('summary', metadata['description_zh']), 'description': metadata['description'], 'license': settings.get('license', ''), 'homepage': settings.get('homepage', ''), 'tags': settings.get('tags', []), 'changelog': settings.get('changelog', '')}
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', payload['slug']) or not 2 <= len(payload['slug']) <= 128:
        raise PublishError('SkillHub slug must be lowercase kebab-case, 2 to 128 characters')
    if not isinstance(payload['tags'], list) or not all(isinstance(tag, str) for tag in payload['tags']):
        raise PublishError('SkillHub tags must be an array of strings')
    for key in set(payload) - {'tags'}:
        if not isinstance(payload[key], str):
            raise PublishError('SkillHub metadata values must be strings')
    if not payload['displayName'].strip() or not payload['summary'].strip():
        raise PublishError('SkillHub displayName and summary are required')
    return payload

class SkillHub:
    def __init__(self, token):
        self.base = 'https://api.skillhub.cn'
        self.token = token

    def request(self, path, body=None, content_type=None):
        headers = {'Authorization': 'Bearer ' + self.token, 'Accept': 'application/json'}
        if content_type:
            headers['Content-Type'] = content_type
        try:
            request = urllib.request.Request(self.base + path, data=body, headers=headers, method='POST' if body is not None else 'GET')
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except (urllib.error.URLError, ValueError) as exc:
            raise PublishError('SkillHub request failed; check account or submission status before retrying') from exc

    def authenticate(self):
        result = self.request('/api/v1/auth/me')
        if not isinstance(result.get('user'), dict) or not result['user'].get('id'):
            raise PublishError('SkillHub identity could not be verified')

    def submit(self, payload, files):
        boundary = 'skillpublisher' + digest(json_bytes(payload))[:24]
        parts = []
        def add(name, data, filename=None, content_type='application/octet-stream'):
            disposition = 'Content-Disposition: form-data; name="' + name + '"'
            if filename is not None:
                if '"' in filename or '\r' in filename or '\n' in filename:
                    raise PublishError('Unsafe multipart filename')
                disposition += '; filename="' + filename + '"'
            parts.append(('--' + boundary + '\r\n' + disposition + '\r\nContent-Type: ' + content_type + '\r\n\r\n').encode() + data + b'\r\n')
        add('payload', json_bytes(payload), content_type='application/json')
        for name, data in sorted(files.items()):
            add('files', data, filename=name, content_type='text/markdown' if name.endswith('.md') else 'application/octet-stream')
        parts.append(('--' + boundary + '--\r\n').encode())
        return self.request('/api/v1/community/skills/publish', b''.join(parts), 'multipart/form-data; boundary=' + boundary)

def publish_skillhub(release, manifest, status):
    previous = status['platforms'].get('skillhub', {})
    if previous.get('state') in ('submitting', 'submitted', 'published', 'uncertain'):
        raise PublishError('SkillHub submission already attempted; inspect its receipt or portal, do not blindly resubmit the same version')
    payload = read_json(release / 'skillhub-submission.json')
    if not payload['license'].strip():
        raise PublishError('Configure SkillHub license identifier before submitting')
    environment = credentials_env()
    token = environment.get('SKILLHUB_TOKEN') or environment.get('SKILLHUB_API_KEY')
    if not token or not token.startswith('skh_'):
        raise PublishError('Set SKILLHUB_TOKEN or SKILLHUB_API_KEY with a skh_ token')
    client = SkillHub(token)
    client.authenticate()
    with zipfile.ZipFile(release / 'skillhub.zip') as archive:
        files = {}
        for name in archive.namelist():
            safe_relative(name)
            if blocked(name):
                raise PublishError('Unsafe SkillHub archive file')
            files[name] = archive.read(name)
    status['platforms']['skillhub'] = {'state': 'submitting'}
    atomic_json(release / 'status.json', status)
    try:
        result = client.submit(payload, files)
        if not result.get('skillId') or not isinstance(result.get('status'), str):
            raise PublishError('SkillHub submission response is incomplete')
        receipt = {key: result[key] for key in ('skillId', 'status', 'publicUrl') if key in result}
        status['platforms']['skillhub'] = {'state': 'published' if result['status'] == 'published' else 'submitted', 'receipt': receipt, 'remote_status': result['status']}
        atomic_json(release / 'status.json', status)
        return receipt
    except (PublishError, OSError, ValueError, KeyError):
        status['platforms']['skillhub'] = {'state': 'uncertain', 'error': 'A submission was attempted. Check the portal before any further action.'}
        atomic_json(release / 'status.json', status)
        raise PublishError('SkillHub submission outcome requires portal verification; automatic resubmission is disabled') from None

def publish(release, platforms=None, execute=False):
    release = Path(release).resolve()
    with lock(release):
        manifest = verify_release(release)
        selected = platforms or list(manifest['platforms'])
        if not selected or set(selected) - set(manifest['platforms']):
            raise PublishError('Select platforms enabled in this release')
        if execute and not manifest.get('license'):
            raise PublishError('Public publication requires an explicit nonempty source license; build drafts remain available')
        state = read_json(release / 'status.json')
        plan, failures = [], []
        for platform in selected:
            plan.append({'platform': platform, 'mode': 'assisted' if platform in PORTALS else ('community-api' if platform == 'skillhub' else 'release-api'), 'execute': execute})
            if not execute:
                continue
            if platform == 'skillhub':
                try:
                    publish_skillhub(release, manifest, state)
                except (PublishError, OSError, ValueError, KeyError) as exc:
                    failures.append(platform + ': ' + (str(exc) if isinstance(exc, PublishError) else 'invalid submission response'))
                continue
            if platform in PORTALS:
                prior = state['platforms'].get(platform, {})
                if prior.get('state') not in ('submitted', 'published'):
                    state['platforms'][platform] = {'state': 'prepared', 'portal': PORTALS[platform]}
                atomic_json(release / 'status.json', state)
                continue
            try:
                function = publish_github if platform == 'github' else publish_gitee
                url = function(release, manifest, manifest['platforms'][platform])
                state['platforms'][platform] = {'state': 'published', 'url': url}
                atomic_json(release / 'status.json', state)
            except (PublishError, OSError, KeyError, ValueError):
                state['platforms'][platform] = {'state': 'failed', 'error': 'Publication or remote verification failed; retry rechecks remote state.'}
                atomic_json(release / 'status.json', state)
                failures.append(platform + ': publication or remote verification failed')
        if failures:
            raise PublishError('Some channels need attention. ' + '; '.join(failures))
        return plan

def record(release, platform, state, url, evidence):
    release = Path(release).resolve()
    with lock(release):
        manifest = verify_release(release)
        if platform not in (*PORTALS, 'skillhub') or platform not in manifest['platforms']:
            raise PublishError('record is only for enabled marketplaces')
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != 'https' or not parsed.hostname or not evidence.strip():
            raise PublishError('Supply HTTPS URL and confirmation evidence')
        status = read_json(release / 'status.json')
        status['platforms'][platform] = {'state': state, 'url': url, 'evidence': evidence, 'source': 'operator-confirmation'}
        atomic_json(release / 'status.json', status)
        return status

def init(path):
    path = Path(path)
    if path.exists():
        raise PublishError('Config already exists')
    config = {'source': './your-skill', 'output': './dist', 'metadata': {'name': 'your-skill', 'version': '0.1.0', 'description': 'Describe the skill', 'description_zh': '技能简介', 'description_en': 'Describe the skill', 'author': 'Your name'}, 'license': None, 'include': ['SKILL.md', 'scripts/**', 'references/**', 'README.md', 'LICENSE'], 'exclude': [], 'platforms': {p: {} for p in PLATFORMS}}
    path.write_bytes(json_bytes(config))
    return str(path.resolve())

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    initial = commands.add_parser('init')
    initial.add_argument('--config', default='publish.json')
    builder = commands.add_parser('build')
    builder.add_argument('--config', default='publish.json')
    for command in ('publish', 'status', 'record'):
        sub = commands.add_parser(command)
        sub.add_argument('--release', required=True)
        if command == 'publish':
            sub.add_argument('--platform', choices=PLATFORMS, action='append')
            sub.add_argument('--execute', action='store_true')
        elif command == 'record':
            sub.add_argument('--platform', choices=(*PORTALS, 'skillhub'), required=True)
            sub.add_argument('--state', choices=('submitted', 'published'), required=True)
            sub.add_argument('--url', required=True)
            sub.add_argument('--evidence', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'init':
            result = init(args.config)
        elif args.command == 'build':
            result = str(build(args.config))
        elif args.command == 'publish':
            result = publish(args.release, args.platform, args.execute)
        elif args.command == 'record':
            result = record(args.release, args.platform, args.state, args.url, args.evidence)
        else:
            verify_release(args.release)
            result = read_json(Path(args.release) / 'status.json')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (PublishError, OSError, KeyError, TypeError, ValueError, zipfile.BadZipFile):
        # Never print exceptions containing request URLs, credentials or file content.
        exc = sys.exc_info()[1]
        print('Error: ' + (str(exc) if isinstance(exc, PublishError) else 'Invalid input or filesystem operation failed'), file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(main())
