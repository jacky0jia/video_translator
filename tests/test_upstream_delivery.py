import asyncio
import hashlib
import io
import json
import ssl
import urllib.error
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import zipfile

from fastapi import HTTPException
from starlette.requests import Request

from test_portable_packaging import ROOT, builder, launcher, load_module

installer = load_module('upstream_installer_under_test', ROOT / 'packaging/install_upstream.py')
install_verify = load_module('upstream_install_verify_under_test', ROOT / 'packaging/verify_upstream_install.py')
verify = load_module('upstream_verifier_under_test', ROOT / 'packaging/portable_verify.py')


class UpstreamDeliveryTests(unittest.TestCase):
    def test_public_builder_rejects_old_installer_tools_and_bootstrap(self):
        for old_installed in (True, False):
            with self.subTest(old_installed=old_installed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                options = self.fixture(root)
                inventory = [{'name': 'demo', 'version': '1'}]
                if old_installed:
                    inventory.append({'name': 'pip', 'version': '25.3'})
                else:
                    bundled = options['python_runtime'] / 'Lib/ensurepip/_bundled'
                    (bundled / 'pip-26.2.0-py3-none-any.whl').rename(bundled / 'pip-24.0-py3-none-any.whl')
                with patch.object(builder, '_python_inventory', return_value=inventory):
                    with self.assertRaisesRegex(RuntimeError, 'Runtime security checks failed'):
                        builder.build_portable(output_dir=root / 'public', **options)
                self.assertFalse((root / 'public').exists())

    def test_script_messages_are_english_and_menu_exit_is_english(self):
        self.assertTrue(builder.PORTABLE_README.isascii())
        self.assertTrue(builder.PUBLIC_RELEASE_NOTE.isascii())
        for name in ('install_upstream.py', 'portable_launcher.py'):
            source = (ROOT / 'packaging' / name).read_text(encoding='utf-8')
            self.assertFalse(any('\u4e00' <= char <= '\u9fff' for char in source), name)
        with patch('builtins.input', return_value='0') as prompt, patch('builtins.print') as output:
            self.assertEqual(installer.interactive_menu({'components': {}}), 0)
        self.assertIn('Select an option', prompt.call_args.args[0])
        self.assertIn('Verify installed components', output.call_args.args[0])

    def test_qwen_sources_follow_relocated_download_manifest_and_are_local_only(self):
        from app.services.upstream_dependencies import qwen_upstream_sources, settings
        from app.api.config import get_qwen_tts_install_status
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'upstream-dependencies.json').write_bytes((ROOT / 'packaging/upstream-dependencies.json').read_bytes())
            with patch.object(settings, 'BASE_DIR', root / 'app'):
                sources = qwen_upstream_sources()
                self.assertEqual(sources['model_directory'], str(root / 'upstream-downloads/qwen-model'))
                self.assertIn('qwen-cuda', sources['cuda']['llama_archive'])
                self.assertIn('qwen-vulkan', sources['vulkan']['llama_archive'])
                self.assertEqual(sources['vulkan']['cuda_archive'], '')
                with patch('app.api.config.inspect_qwen_tts_installation', return_value={'installed': False}):
                    local = Request({'type': 'http', 'client': ('127.0.0.1', 1000)})
                    self.assertEqual(asyncio.run(get_qwen_tts_install_status(local))['upstream_sources'], sources)
                with self.assertRaises(HTTPException):
                    asyncio.run(get_qwen_tts_install_status(Request({'type': 'http', 'client': ('192.0.2.1', 1000)})))
                (root / 'upstream-dependencies.json').unlink()
                self.assertEqual(qwen_upstream_sources(), {})

    def test_menu_decline_never_downloads_and_component_check_is_selective(self):
        manifest = {'components': {'ffmpeg': {'files': []}}}
        with patch('builtins.input', side_effect=['1', 'n', '5', 'ffmpeg kokoro', '0']), patch.object(installer, 'install_component') as download, patch.object(installer, 'verify_installed', return_value=0) as check:
            self.assertEqual(installer.interactive_menu(manifest), 0)
        download.assert_not_called()
        check.assert_called_once_with(['ffmpeg', 'kokoro'])

    def test_menu_qwen_download_is_not_reported_as_installed(self):
        manifest = {'components': {'qwen-model': {}, 'qwen-cuda': {}}}
        with patch('builtins.input', side_effect=['3', 'y', '0']), patch.object(installer, 'install_component') as download, patch.object(installer, 'verify_installed') as check:
            installer.interactive_menu(manifest)
        self.assertEqual([call.args[0] for call in download.call_args_list], ['qwen-model', 'qwen-cuda'])
        check.assert_not_called()

    def test_download_progress_and_hash_validation_with_unknown_length(self):
        class Response(io.BytesIO):
            headers = {}
            def geturl(self):
                return 'https://official.invalid/file'
        with tempfile.TemporaryDirectory() as directory:
            payload = b'good'
            item = {'name': 'file.bin', 'url': 'https://official.invalid/file', 'sha256': hashlib.sha256(payload).hexdigest()}
            with patch.object(installer.urllib.request, 'urlopen', return_value=Response(payload)), patch('builtins.print') as output:
                self.assertEqual(installer.download(item, Path(directory)).read_bytes(), payload)
            self.assertTrue(any('MiB' in str(call) for call in output.call_args_list))
            self.assertTrue(any('SHA-256 matched' in str(call) for call in output.call_args_list))

    def test_download_loads_bundled_roots_when_system_trust_is_empty(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self.assertEqual(context.cert_store_stats()['x509_ca'], 0)
        with patch.object(installer.ssl, 'create_default_context', return_value=context):
            loaded = installer.download_ssl_context()
        self.assertIs(loaded, context)
        self.assertGreater(context.cert_store_stats()['x509_ca'], 0)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_certificate_failure_stops_download_without_insecure_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item = {'name': 'file.bin', 'url': 'https://official.invalid/file', 'sha256': '0' * 64}
            error = urllib.error.URLError(ssl.SSLCertVerificationError('untrusted issuer'))
            with patch.object(installer.urllib.request, 'urlopen', side_effect=error) as network:
                with self.assertRaises(urllib.error.URLError):
                    installer.download(item, root)
            self.assertEqual(network.call_count, 1)
            context = network.call_args.kwargs['context']
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)
            self.assertEqual(list(root.iterdir()), [])

    def test_install_verifier_detects_corrupt_ffprobe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entries = []
            for name in ('ffmpeg.exe', 'ffprobe.exe'):
                path = root / 'app/ffmpeg' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(name.encode())
                entries.append({'path': path.relative_to(root).as_posix(), 'sha256': builder.sha256(path)})
            (root / 'upstream-dependencies.json').write_text(json.dumps({'components': {'ffmpeg': {'installed_files': entries}}}))
            self.assertTrue(install_verify.verify_files(root, ['ffmpeg'])['ffmpeg']['binary_hashes'])
            (root / 'app/ffmpeg/ffprobe.exe').write_bytes(b'corrupted')
            with self.assertRaisesRegex(RuntimeError, 'hash mismatch.*ffprobe'):
                install_verify.verify_files(root, ['ffmpeg'])

    def test_install_verifier_compares_loader_against_pinned_wheel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_dir = root / 'models/kokoro'
            model_dir.mkdir(parents=True)
            entries = []
            for name in ('kokoro-v1.0.onnx', 'voices-v1.0.bin'):
                path = model_dir / name
                path.write_bytes(name.encode())
                entries.append({'name': name, 'sha256': builder.sha256(path)})
            wheel = model_dir / 'espeakng_loader-0.2.4-py3-none-win_amd64.whl'
            with zipfile.ZipFile(wheel, 'w') as source:
                source.writestr('espeakng_loader/__init__.py', b'loader')
            entries.append({'name': wheel.name, 'sha256': builder.sha256(wheel)})
            installed = root / 'runtime/Lib/site-packages/espeakng_loader/__init__.py'
            installed.parent.mkdir(parents=True)
            installed.write_bytes(b'loader')
            (root / 'upstream-dependencies.json').write_text(json.dumps({'components': {'kokoro': {'files': entries}}}))
            self.assertEqual(install_verify.verify_files(root, ['kokoro'])['kokoro']['loader_package_files'], 1)
            installed.write_bytes(b'corrupted')
            with self.assertRaisesRegex(RuntimeError, 'hash mismatch'):
                install_verify.verify_files(root, ['kokoro'])

    def test_install_verifier_rejects_changed_qwen_model_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / 'models/qwen3-tts'
            bundle.mkdir(parents=True)
            (bundle / 'bundle.json').write_text(json.dumps({'model_path': '../external.gguf'}))
            (root / 'upstream-dependencies.json').write_text(json.dumps({'components': {'qwen-model': {'files': [{'name': 'model.gguf'}, {'name': 'mmproj.gguf'}]}}}))
            with self.assertRaisesRegex(RuntimeError, 'Unexpected installed Qwen model path'):
                install_verify.verify_files(root, ['qwen'])

    def fixture(self, root):
        runtime = root / 'python'
        site = runtime / 'Lib/site-packages'
        demo = site / 'demo-1.dist-info'
        demo.mkdir(parents=True)
        (demo / 'METADATA').write_text('Name: demo\nVersion: 1\n')
        (demo / 'LICENSE').write_text('MIT')
        (runtime / 'LICENSE_PYTHON.txt').write_text('Python')
        (runtime / 'python.exe').write_bytes(b'python')
        ensurepip = runtime / 'Lib/ensurepip/_bundled'
        ensurepip.mkdir(parents=True)
        (ensurepip / 'pip-26.2.0-py3-none-any.whl').write_bytes(b'python bootstrap wheel')
        loader = site / 'espeakng_loader-0.2.4.dist-info'
        loader.mkdir()
        (loader / 'METADATA').write_text('Name: espeakng-loader\nVersion: 0.2.4\n')
        (loader / 'RECORD').write_text('espeakng_loader/__init__.py,,\nespeakng_loader/libespeak-ng.dll,,\nespeakng_loader-0.2.4.dist-info/METADATA,,\n')
        (site / 'espeakng_loader').mkdir()
        (site / 'espeakng_loader/__init__.py').write_text('loader')
        (site / 'espeakng_loader/libespeak-ng.dll').write_bytes(b'dll')
        asr = root / 'asr'
        asr.mkdir()
        (asr / 'model.bin').write_bytes(b'asr')
        voices = root / 'voices'
        voices.mkdir()
        entries = []
        for index in range(8):
            voice = voices / f'{index}.wav'
            voice.write_bytes(b'voice')
            entries.append({'id': str(index), 'file': voice.name, 'sha256': builder.sha256(voice)})
        (voices / 'SOURCES.json').write_text(json.dumps({'voices': entries}))
        frontend = root / 'frontend'
        frontend.mkdir()
        (frontend / 'index.html').write_text('<html></html>')
        materials = root / 'materials'
        materials.mkdir()
        attachments = []
        for role in ('license', 'model_card'):
            path = materials / role
            path.write_text(role)
            attachments.append({'role': role, 'path': role, 'sha256': builder.sha256(path)})
        (materials / 'release-materials.json').write_text(json.dumps({'schema_version': 1, 'components': [{
            'id': 'asr-model', 'license_expression': 'MIT', 'source_url': 'https://example.invalid/asr',
            'source_revision': 'fixed', 'artifacts': [{'sha256': builder.sha256(asr / 'model.bin')}],
            'attachments': attachments,
        }]}))
        return dict(source_root=ROOT, python_runtime=runtime, asr_model=asr, voices=voices,
                    ffmpeg_dir=root / 'missing', kokoro=root / 'missing', qwen_bundle=root / 'missing',
                    frontend_dist=frontend, release_materials=materials, commit='fixture',
                    public_distribution=True, upstream_dependencies=True)

    def test_public_core_audits_actual_payload_and_starts_without_optional_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = self.fixture(root)
            with patch.object(builder, '_python_inventory', return_value=[{'name': 'demo', 'version': '1'}]):
                bundle = builder.build_portable(output_dir=root / 'public', **options)
            manifest = verify.verify_manifest(bundle)
            self.assertEqual(manifest['dependency_delivery'], 'user_upstream_install')
            self.assertFalse((bundle / 'models/qwen3-tts').exists())
            self.assertFalse((bundle / 'models/kokoro').exists())
            self.assertFalse((bundle / 'app/ffmpeg').exists())
            self.assertIn('install_upstream.py', (bundle / 'install-upstream.bat').read_text())
            self.assertIn('Setup menu', (bundle / 'README-PORTABLE.md').read_text())
            self.assertEqual((bundle / 'README.md').read_bytes(), (bundle / 'README-PORTABLE.md').read_bytes())
            self.assertTrue((bundle / 'README-PORTABLE.zh-CN.md').is_file())
            self.assertFalse((bundle / 'runtime/Lib/site-packages/espeakng_loader').exists())
            self.assertTrue((bundle / 'runtime/Lib/ensurepip/_bundled/pip-26.2.0-py3-none-any.whl').is_file())
            licenses = json.loads((bundle / 'licenses/THIRD-PARTY-MANIFEST.json').read_text())
            self.assertEqual([p['name'] for p in licenses['python']['packages']], ['demo'])
            self.assertEqual([p['id'] for p in licenses['external']['components']], ['asr-model'])
            launcher.validate_layout(bundle)
            rogue = bundle / 'runtime/ffmpeg.exe'
            rogue.write_bytes(b'rogue')
            with self.assertRaisesRegex(RuntimeError, 'upstream-only'):
                verify.verify_manifest(bundle)

    def test_loader_record_cannot_silently_exclude_shared_or_escaping_files(self):
        with tempfile.TemporaryDirectory() as directory:
            options = self.fixture(Path(directory))
            record = options['python_runtime'] / 'Lib/site-packages/espeakng_loader-0.2.4.dist-info/RECORD'
            record.write_text('../shared.py,,\n')
            with self.assertRaisesRegex(ValueError, 'unexpected ownership'):
                builder.build_portable(output_dir=Path(directory) / 'public', **options)
            self.assertFalse((Path(directory) / 'public').exists())

    def test_core_server_smoke_requires_uninstalled_qwen_and_actionable_guide(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'upstream-dependencies.json').write_text('{}')
            process = MagicMock()
            process.poll.return_value = None
            response = MagicMock()
            response.__enter__.return_value.status = 200
            payloads = [{'installed': False, 'managed': False}, {'voices': []},
                        {'upstream_dependencies': {'user_install': True, 'missing': ['Qwen']}}]
            with patch.object(verify.subprocess, 'Popen', return_value=process), patch.object(verify.urllib.request, 'urlopen', return_value=response), patch.object(verify, '_read_json', side_effect=payloads):
                result = verify.smoke_server(root)
            self.assertEqual(result['voice_ids'], [])
            process.terminate.assert_called_once()
            with patch.object(verify.subprocess, 'Popen', return_value=process), patch.object(verify.urllib.request, 'urlopen', return_value=response), patch.object(verify, '_read_json', side_effect=[{'installed': True}, {'voices': []}]):
                with self.assertRaisesRegex(RuntimeError, 'unexpectedly has a Qwen installation'):
                    verify.smoke_server(root)

    def test_core_still_blocks_unlicensed_shipped_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            options = self.fixture(Path(directory))
            (options['python_runtime'] / 'Lib/site-packages/demo-1.dist-info/LICENSE').unlink()
            with self.assertRaisesRegex(RuntimeError, 'demo 1'):
                builder.build_portable(output_dir=Path(directory) / 'public', **options)

    def test_download_hash_failure_cleans_temporary_and_preserves_existing(self):
        class Response(io.BytesIO):
            def geturl(self):
                return 'https://official.invalid/file'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item = {'name': 'model.bin', 'url': 'https://official.invalid/file', 'sha256': hashlib.sha256(b'good').hexdigest()}
            with patch.object(installer.urllib.request, 'urlopen', return_value=Response(b'bad')):
                with self.assertRaisesRegex(RuntimeError, 'hash mismatch'):
                    installer.download(item, root)
            self.assertEqual(list(root.iterdir()), [])
            with patch.object(installer.urllib.request, 'urlopen', return_value=Response(b'good')):
                path = installer.download(item, root)
            with patch.object(installer.urllib.request, 'urlopen') as network:
                self.assertEqual(installer.download(item, root), path)
                network.assert_not_called()
            path.write_bytes(b'other')
            with self.assertRaisesRegex(RuntimeError, 'different hash'):
                installer.download(item, root)
            self.assertEqual(path.read_bytes(), b'other')

    def test_ffmpeg_installer_keeps_license_and_does_not_extract_arbitrary_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'ffmpeg.zip'
            with zipfile.ZipFile(archive, 'w') as output:
                for name in ('bin/ffmpeg.exe', 'bin/ffprobe.exe', 'LICENSE', 'README.txt'):
                    output.writestr('release/' + name, name)
                output.writestr('../escaped.py', 'bad')
            installer.install_ffmpeg(archive, root)
            self.assertTrue((root / 'app/ffmpeg/LICENSE').is_file())
            self.assertFalse((root / 'escaped.py').exists())
            (root / 'app/ffmpeg/ffprobe.exe').write_bytes(b'different')
            with self.assertRaisesRegex(RuntimeError, 'Refusing to replace'):
                installer.install_ffmpeg(archive, root)

    def test_qwen_downloads_match_existing_fixed_install_inputs(self):
        manifest = json.loads((ROOT / 'packaging/upstream-dependencies.json').read_text())
        pinned = json.loads((ROOT / 'packaging/llama-tts/package-manifest.b10792.json').read_text())
        downloads = {f['name']: f['sha256'] for c in manifest['components'].values() for f in c['files']}
        for archive in pinned['archives'].values():
            self.assertEqual(downloads[archive['filename']], archive['sha256'])
        self.assertEqual(downloads[pinned['model']['filename']], pinned['model']['sha256'])
        self.assertEqual(downloads[pinned['model']['mmproj_filename']], pinned['model']['mmproj_sha256'])

    def test_public_kokoro_missing_loader_is_actionable_and_never_downloads(self):
        from app.services.tts.kokoro import KokoroTTSProvider, settings
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'upstream-dependencies.json').write_text('{}')
            with patch.object(settings, 'BASE_DIR', root / 'app'), patch.object(KokoroTTSProvider, '_kokoro_instance', None), patch('importlib.util.find_spec', return_value=None), patch.object(KokoroTTSProvider, '_download_file') as download:
                with self.assertRaisesRegex(RuntimeError, 'install_upstream.py kokoro'):
                    KokoroTTSProvider._get_kokoro()
                download.assert_not_called()

    def test_remote_clients_cannot_read_install_guide_or_dependency_status(self):
        from app.api.config import get_config, get_upstream_install_guide
        request = Request({'type': 'http', 'client': ('192.0.2.1', 1000)})
        with patch('app.services.upstream_dependencies.upstream_dependency_status') as status:
            result = asyncio.run(get_config(request))
            self.assertNotIn('upstream_dependencies', result)
            status.assert_not_called()
        with self.assertRaises(HTTPException) as context:
            asyncio.run(get_upstream_install_guide(request))
        self.assertEqual(context.exception.status_code, 403)


if __name__ == '__main__':
    unittest.main()
