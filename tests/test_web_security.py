"""HTTP regressions for the local app's browser and file trust boundaries."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, Request, UploadFile, HTTPException
from fastapi.staticfiles import StaticFiles

from app.api import transcribe, translation
from app.api.tasks import _video_url
from app.core.file_security import MEDIA_EXTENSIONS, save_upload, upload_name
from app.core.web_security import LocalWebSecurityMiddleware
from app.main import video_stream


class WebSecurityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.uploads = self.root / 'uploads'
        self.output = self.root / 'output'
        self.uploads.mkdir()
        self.output.mkdir()
        for key, value in (('UPLOAD_DIR', self.uploads), ('OUTPUT_DIR', self.output)):
            mock = patch('app.core.config.settings.' + key, value)
            mock.start()
            self.addCleanup(mock.stop)
        self.app = FastAPI()
        self.app.add_middleware(LocalWebSecurityMiddleware)
        self.app.include_router(transcribe.router, prefix='/api')
        self.app.include_router(translation.router, prefix='/api')
        self.app.add_api_route('/video/{filename}', video_stream)
        self.app.mount('/static/uploads', StaticFiles(directory=self.uploads))
        self.app.mount('/static/output', StaticFiles(directory=self.output))

        @self.app.api_route('/api/probe', methods=['GET', 'POST'])
        async def probe(request: Request):
            await request.body()
            return {'ok': True}

        @self.app.get('/')
        async def index():
            return {'ok': True}

    def client(self, host='127.0.0.1', url='http://127.0.0.1:8769'):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=(host, 1234)), base_url=url)

    async def test_local_browser_and_local_cli_are_allowed(self):
        for url in ('http://127.0.0.1:8769', 'http://localhost:8769', 'http://[::1]:8769'):
            async with self.client(url=url) as client:
                for headers in ({}, {'Origin': url, 'Sec-Fetch-Site': 'same-origin'}, {'Referer': url + '/settings'}):
                    response = await client.post('/api/probe', json={'value': 1}, headers=headers)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers['x-content-type-options'], 'nosniff')

    async def test_untrusted_browser_origins_cannot_read_or_mutate(self):
        async with self.client() as client:
            for origin in ('https://evil.example', 'null', 'http://127.0.0.1:8000',
                           'http://127.0.0.1.evil.example:8769', 'http://localhost:8769', 'http://user@127.0.0.1:8769'):
                for method in ('GET', 'POST'):
                    response = await client.request(method, '/api/probe', headers={'Origin': origin})
                    self.assertEqual(response.status_code, 403, origin)
            for headers in ({'Sec-Fetch-Site': 'cross-site'}, {'Sec-Fetch-Site': 'same-site'},
                            {'Referer': 'https://evil.example/'}, {'Origin': 'http://127.0.0.1:8769/'}):
                response = await client.get('/api/probe', headers=headers)
                self.assertEqual(response.status_code, 403)

    async def test_dns_rebinding_hosts_and_duplicate_headers_are_rejected(self):
        async with self.client() as client:
            for host in ('evil.example:8769', '127.0.0.1.evil.example', 'localhost@evil.example', '[::1', '127.0.0.1:bad'):
                response = await client.get('/api/probe', headers={'Host': host})
                self.assertEqual(response.status_code, 400)
            response = await client.get('/api/probe', headers=[('Host', '127.0.0.1:8769'), ('Host', 'evil.example')])
            self.assertEqual(response.status_code, 400)
            response = await client.post('/api/probe', headers=[('Origin', 'http://127.0.0.1:8769'), ('Origin', 'https://evil.example')])
            self.assertEqual(response.status_code, 403)

    async def test_remote_clients_cannot_spoof_loopback_with_headers(self):
        async with self.client(host='192.0.2.10') as client:
            response = await client.get('/api/probe', headers={'X-Forwarded-For': '127.0.0.1', 'X-Real-IP': '127.0.0.1'})
            self.assertEqual(response.status_code, 403)

    async def test_top_level_visit_does_not_authorize_cross_site_api_navigation(self):
        async with self.client() as client:
            headers = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate'}
            self.assertEqual((await client.get('/', headers=headers)).status_code, 200)
            self.assertEqual((await client.get('/api/probe', headers=headers)).status_code, 403)

    async def test_declared_and_chunked_body_limits_are_enforced(self):
        async with self.client() as client:
            response = await client.post('/api/probe', headers={'Content-Length': str(1024**2 + 1)})
            self.assertEqual(response.status_code, 413)
            for value in ('-1', 'abc', '9' * 100):
                response = await client.post('/api/probe', headers={'Content-Length': value})
                self.assertEqual(response.status_code, 400)

            async def chunks():
                yield b'x' * 1024**2
                yield b'x'
            response = await client.post('/api/probe', content=chunks())
            self.assertEqual(response.status_code, 413)

    async def test_same_name_uploads_keep_original_display_name_and_distinct_media(self):
        with patch('app.api.transcribe.run_transcription_task', new_callable=AsyncMock), patch(
            'app.api.transcribe.history_manager.create_task', return_value='one'
        ) as create:
            async with self.client() as client:
                for data in (b'first', b'second'):
                    response = await client.post('/api/transcribe', files={'file': ('clip.mp4', data, 'video/mp4')})
                    self.assertEqual(response.status_code, 200)
        files = list(self.uploads.iterdir())
        self.assertEqual(len(files), 2)
        self.assertEqual({p.read_bytes() for p in files}, {b'first', b'second'})
        self.assertEqual([c.args[0] for c in create.call_args_list], ['clip.mp4', 'clip.mp4'])
        for call in create.call_args_list:
            task = {'filename': 'clip.mp4', 'upload_path': call.kwargs['source_path']}
            async with self.client() as client:
                response = await client.get(_video_url(task))
            self.assertIn(response.content, (b'first', b'second'))

    async def test_executable_and_windows_stream_upload_names_are_rejected(self):
        async with self.client() as client:
            for name in ('evil.html', 'evil.svg', 'clip.mp4:evil.mp4', 'clip.mp4.', 'evil.js'):
                response = await client.post('/api/transcribe', files={'file': (name, b'content')})
                self.assertEqual(response.status_code, 400, name)
        self.assertEqual(list(self.uploads.iterdir()), [])
        with self.assertRaises(HTTPException):
            upload_name('clip\x00.mp4', MEDIA_EXTENSIONS)

    async def test_path_components_do_not_control_stored_upload_name(self):
        file = UploadFile(io.BytesIO(b'content'), filename=r'C:\outside\clip.mp4')
        path, name = await save_upload(file, self.uploads, MEDIA_EXTENSIONS, 100)
        self.assertEqual(name, 'clip.mp4')
        self.assertEqual(path.parent, self.uploads)
        self.assertNotEqual(path.name, name)

    async def test_failed_uploads_leave_no_partial_file(self):
        for data in (b'', b'too large'):
            file = UploadFile(io.BytesIO(data), filename='clip.mp4')
            with self.assertRaises(HTTPException):
                await save_upload(file, self.uploads, MEDIA_EXTENSIONS, 2)
            self.assertEqual(list(self.uploads.iterdir()), [])

    async def test_old_uploaded_html_is_sandboxed_and_not_cached(self):
        (self.uploads / 'old.html').write_text('<script>fetch("/api/config")</script>', encoding='utf-8')
        async with self.client() as client:
            response = await client.get('/static/uploads/old.html')
        self.assertEqual(response.status_code, 200)
        self.assertIn('sandbox', response.headers['content-security-policy'])
        self.assertIn("default-src 'none'", response.headers['content-security-policy'])
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.headers['x-frame-options'], 'DENY')

    async def test_translation_cannot_read_outside_output_or_inject_filename(self):
        outside = self.root / 'private.json'
        outside.write_text('{}', encoding='utf-8')
        with patch('app.api.translation.translation_service.translate_full_result', new_callable=AsyncMock) as provider:
            async with self.client() as client:
                response = await client.post('/api/translate', data={'json_path': str(outside)})
                self.assertEqual(response.status_code, 404)
                for field, value in (('target_lang', '../../private'), ('task_id', '../escape')):
                    response = await client.post('/api/translate', data={'json_path': str(outside), field: value})
                    self.assertEqual(response.status_code, 400)
            provider.assert_not_called()

    async def test_output_previews_support_ranges_and_reject_missing_files(self):
        (self.output / 'sample.srt').write_bytes(b'0123456789')
        (self.output / 'sample.wav').write_bytes(b'RIFF0123456789')
        (self.output / 'sample.mp4').write_bytes(b'video0123456789')
        async with self.client() as client:
            for name in ('sample.srt', 'sample.wav', 'sample.mp4'):
                response = await client.get(f'/static/output/{name}', headers={'Range': 'bytes=2-5'})
                self.assertEqual(response.status_code, 206, name)
                self.assertEqual(response.content, (self.output / name).read_bytes()[2:6])
                self.assertIn('sandbox', response.headers['content-security-policy'])
            self.assertEqual((await client.get('/static/output/missing.wav')).status_code, 404)

    async def test_video_ranges_include_suffix_and_reject_invalid_values(self):
        (self.uploads / 'clip.mp4').write_bytes(b'0123456789')
        async with self.client() as client:
            for value, expected in (('bytes=2-4', b'234'), ('bytes=7-', b'789'), ('bytes=-3', b'789'), ('bytes=-20', b'0123456789')):
                response = await client.get('/video/clip.mp4', headers={'Range': value})
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.content, expected)
                self.assertEqual(int(response.headers['content-length']), len(expected))
            for value in ('bytes=7-2', 'bytes=10-', 'bytes=-0', 'bytes=-', 'bytes=0-1,3-4', 'bytes=-1-5', 'bytes=' + '9'*100 + '-'):
                response = await client.get('/video/clip.mp4', headers={'Range': value})
                self.assertEqual(response.status_code, 416, value)
                self.assertEqual(response.headers['content-range'], 'bytes */10')

    async def test_video_cannot_follow_symlink_to_neighbor_directory(self):
        outside = self.root / 'uploads-private'
        outside.mkdir()
        target = outside / 'clip.mp4'
        target.write_bytes(b'private')
        try:
            (self.uploads / 'link.mp4').symlink_to(target)
        except OSError:
            self.skipTest('Symlink creation is unavailable on this host')
        async with self.client() as client:
            response = await client.get('/video/link.mp4')
        self.assertEqual(response.status_code, 404)


if __name__ == '__main__':
    unittest.main()
