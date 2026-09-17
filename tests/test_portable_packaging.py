import importlib.util
import json
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


builder = load_module("portable_builder_under_test", ROOT / "packaging/portable_builder.py")
licenses = load_module("portable_licenses_under_test", ROOT / "packaging/portable_licenses.py")
launcher = load_module("portable_launcher_under_test", ROOT / "packaging/portable_launcher.py")
runtime_smoke = load_module("portable_runtime_smoke_under_test", ROOT / "packaging/portable_runtime_smoke.py")


class PortablePackagingTests(unittest.TestCase):
    def test_legacy_egg_metadata_is_audited_and_missing_license_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / 'runtime'
            info = runtime / 'Lib/site-packages/demo-1.2-py3.11.egg-info'
            info.mkdir(parents=True)
            (info / 'PKG-INFO').write_text('Name: demo\nVersion: 1.2\nLicense: MIT\n', encoding='utf-8')
            (runtime / 'LICENSE_PYTHON.txt').write_text('python license', encoding='utf-8')
            report, blockers = licenses.collect_python_licenses(runtime, root / 'missing')
            self.assertEqual([(p['name'], p['version']) for p in report['packages']], [('demo', '1.2')])
            self.assertEqual(len(blockers), 1)
            self.assertIn('demo 1.2', blockers[0])
            (info / 'LICENSE').write_text('demo license', encoding='utf-8')
            report, blockers = licenses.collect_python_licenses(runtime, root / 'complete')
            self.assertEqual(blockers, [])
            self.assertEqual(len(report['packages'][0]['attachments']), 1)

    @unittest.skipUnless(Path(sys.executable).name.lower() == 'python.exe', 'Windows portable runtime')
    def test_real_inventory_does_not_generate_bytecode_or_change_between_reads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / 'inventory-cache'
            with patch.dict(os.environ, {'PYTHONPYCACHEPREFIX': str(cache), 'PYTHONDONTWRITEBYTECODE': ''}):
                first = builder._python_inventory(Path(sys.executable).parent)
                second = builder._python_inventory(Path(sys.executable).parent)
            self.assertTrue(first)
            self.assertEqual(first, second)
            self.assertFalse(list(cache.rglob('*.pyc')))

    def test_release_checklist_disables_bytecode_for_runtime_smoke(self) -> None:
        checklist = (ROOT / "packaging" / "RELEASE-CHECKLIST.md").read_text(encoding="utf-8")
        self.assertIn("runtime\\python.exe -B", checklist)

    def test_portable_config_and_launcher_are_relocatable(self) -> None:
        self.assertNotIn(str(ROOT), builder.PORTABLE_CONFIG)
        self.assertIn("ASR_MODEL_PATH: models/faster-whisper-small", builder.PORTABLE_CONFIG)
        self.assertIn("FFMPEG_PATH: ffmpeg/ffmpeg.exe", builder.PORTABLE_CONFIG)
        self.assertIn('cd /d "%~dp0"', builder.START_BATCH)
        self.assertIn("set PYTHONDONTWRITEBYTECODE=1", builder.START_BATCH)
        self.assertIn('"%~dp0runtime\\python.exe"', builder.START_BATCH)

    def test_application_copy_excludes_history_frontend_source_and_runtime_media(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, destination = root / "app", root / "portable-app"
            for name in (
                "main.py", "history.json", "frontend/src/main.jsx", "frontend/dist/index.html",
                "output/video.mp4", "temp/work.txt", "uploads/input.mp4", "__pycache__/main.pyc",
            ):
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("content", encoding="utf-8")
            builder._copy_application(source, destination)
            files = [path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()]
            self.assertEqual(files, ["main.py"])

    def test_layout_validation_requires_runtime_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "Portable bundle is incomplete"):
                launcher.validate_layout(root)
            for name in (
                "app/main.py", "app/frontend/dist/index.html", "app/ffmpeg/ffmpeg.exe",
                "app/ffmpeg/ffprobe.exe", "models/faster-whisper-small/model.bin",
                "models/voices/librivox_public_domain/SOURCES.json", "runtime/python.exe",
            ):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"test")
            launcher.validate_layout(root)

    def test_voice_catalog_copy_checks_all_hashes_and_omits_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, destination = root / "source", root / "destination"
            source.mkdir()
            voices = []
            for index in range(8):
                path = source / f"voice-{index}.wav"
                path.write_bytes(f"voice-{index}".encode())
                voices.append({"id": f"voice-{index}", "file": path.name, "sha256": builder.sha256(path)})
            (source / "SOURCES.json").write_text(json.dumps({"voices": voices}), encoding="utf-8")
            extra = source / "source" / "original.mp3"
            extra.parent.mkdir()
            extra.write_bytes(b"large source")
            builder._copy_voice_catalog(source, destination)
            self.assertEqual(len(list(destination.glob("*.wav"))), 8)
            self.assertFalse((destination / "source").exists())
            (source / "voice-0.wav").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "Voice checksum mismatch"):
                builder._copy_voice_catalog(source, root / "bad")

    def test_portable_copies_both_qwen_backend_manifests_and_not_binaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "support"
            builder._copy_qwen_install_support(ROOT, destination)
            self.assertEqual(
                {path.name for path in destination.iterdir()},
                set(builder.QWEN_INSTALL_SUPPORT_FILES),
            )
            self.assertTrue(
                (destination / "runtime-manifest-vulkan.b10792.json").is_file()
            )
            self.assertFalse(any(
                path.suffix.lower() in {".dll", ".exe"}
                for path in destination.iterdir()
            ))

    def test_manifest_verifier_rejects_extra_and_changed_files(self) -> None:
        verify = load_module("portable_verify_under_test", ROOT / "packaging/portable_verify.py")
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            payload = bundle / "payload.txt"
            payload.write_text("valid", encoding="utf-8")
            manifest = {
                "distribution_scope": "local_acceptance_only",
                "files": [{"path": "payload.txt", "bytes": payload.stat().st_size,
                           "sha256": builder.sha256(payload)}],
            }
            (bundle / "portable-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            verify.verify_manifest(bundle)
            payload.write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                verify.verify_manifest(bundle)

    def test_validate_port_rejects_invalid_and_occupied_ports(self) -> None:
        with self.assertRaises(ValueError):
            launcher.validate_port(0)
        with patch("socket.socket.bind", side_effect=OSError("busy")):
            with self.assertRaisesRegex(RuntimeError, "already in use"):
                launcher.validate_port(8769)

    def test_build_requires_clean_tracked_source(self) -> None:
        with patch("subprocess.run") as run:
            run.return_value.stdout = " M app/main.py\n"
            with self.assertRaisesRegex(RuntimeError, "Commit tracked changes"):
                builder._require_clean_tracked_tree(ROOT)

    def test_python_and_frontend_license_collection_copies_exact_attachments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            site = runtime / "Lib/site-packages"
            info = site / "demo-1.2.dist-info"
            info.mkdir(parents=True)
            (info / "METADATA").write_text(
                "Name: demo\nVersion: 1.2\nLicense-Expression: MIT\n", encoding="utf-8"
            )
            (info / "LICENSE").write_text("demo license", encoding="utf-8")
            (runtime / "LICENSE_PYTHON.txt").write_text("python license", encoding="utf-8")
            frontend = root / "frontend"
            module = frontend / "node_modules/react"
            module.mkdir(parents=True)
            (module / "LICENSE").write_text("react license", encoding="utf-8")
            (frontend / "package-lock.json").write_text(json.dumps({
                "lockfileVersion": 3,
                "packages": {"node_modules/react": {"version": "18.3.1", "license": "MIT"}},
            }), encoding="utf-8")
            python_report, python_blockers = licenses.collect_python_licenses(runtime, root / "licenses")
            frontend_report, frontend_blockers = licenses.collect_frontend_licenses(frontend, root / "licenses")
            self.assertFalse(python_blockers)
            self.assertFalse(frontend_blockers)
            self.assertEqual(python_report["packages"][0]["name"], "demo")
            self.assertEqual(frontend_report["packages"][0]["name"], "react")
            self.assertTrue((root / "licenses/python/demo-1.2/demo-1.2.dist-info/LICENSE").is_file())

    def test_external_release_materials_validate_asset_hashes_and_required_roles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = {}
            components = []
            for component, roles in licenses.REQUIRED_EXTERNAL_ROLES.items():
                artifact = root / f"{component}.bin"
                artifact.write_bytes(component.encode())
                assets[component] = [artifact]
                attachments = []
                for role in roles:
                    path = root / component / f"{role}.txt"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(role, encoding="utf-8")
                    attachments.append({"role": role, "path": path.relative_to(root).as_posix(),
                                        "sha256": licenses.sha256(path)})
                components.append({
                    "id": component, "license_expression": "MIT",
                    "source_url": "https://example.invalid/source", "source_revision": "v1",
                    "artifacts": [{"sha256": licenses.sha256(artifact)}],
                    "attachments": attachments,
                })
            supplement = root / "supplement/LICENSE"
            supplement.parent.mkdir(parents=True)
            supplement.write_text("license", encoding="utf-8")
            components.append({
                "id": "python:demo-1.2", "license_expression": "MIT",
                "source_url": "https://example.invalid/demo", "source_revision": "1.2",
                "attachments": [{"role": "license", "path": "supplement/LICENSE",
                                 "sha256": licenses.sha256(supplement)}],
            })
            (root / "release-materials.json").write_text(json.dumps({
                "schema_version": 1, "components": components,
            }), encoding="utf-8")
            report, blockers = licenses.collect_external_materials(root, root / "out", assets)
            self.assertFalse(blockers)
            self.assertEqual(len(report["components"]), 5)
            components[0]["artifacts"][0]["sha256"] = "0" * 64
            (root / "release-materials.json").write_text(json.dumps({
                "schema_version": 1, "components": components,
            }), encoding="utf-8")
            _, blockers = licenses.collect_external_materials(root, root / "out-2", assets)
            self.assertTrue(any("artifact hashes" in blocker for blocker in blockers))

    def test_public_manifest_verifier_rejects_unresolved_blockers(self) -> None:
        verify = load_module("portable_verify_public_under_test", ROOT / "packaging/portable_verify.py")
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            (bundle / "portable-manifest.json").write_text(json.dumps({
                "distribution_scope": "public_release",
                "public_distribution_ready": False,
                "public_distribution_blockers": ["missing"],
                "files": [],
            }), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unresolved distribution blockers"):
                verify.verify_manifest(bundle)
            licenses_path = bundle / "licenses/THIRD-PARTY-MANIFEST.json"
            licenses_path.parent.mkdir()
            licenses_path.write_text(json.dumps({
                "public_distribution_blockers": [],
            }), encoding="utf-8")
            (bundle / "portable-manifest.json").write_text(json.dumps({
                "distribution_scope": "public_release",
                "public_distribution_ready": True,
                "public_distribution_blockers": [],
                "files": [{
                    "path": "licenses/THIRD-PARTY-MANIFEST.json",
                    "bytes": licenses_path.stat().st_size,
                    "sha256": verify.sha256(licenses_path),
                }],
            }), encoding="utf-8")
            verify.verify_manifest(bundle)

    def test_supplemental_python_license_can_close_public_material_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            info = runtime / "Lib/site-packages/demo-1.2.dist-info"
            info.mkdir(parents=True)
            (info / "METADATA").write_text("Name: demo\nVersion: 1.2\n", encoding="utf-8")
            (runtime / "LICENSE_PYTHON.txt").write_text("python", encoding="utf-8")
            frontend = root / "frontend"
            module = frontend / "node_modules/react"
            module.mkdir(parents=True)
            (module / "LICENSE").write_text("react", encoding="utf-8")
            (frontend / "package-lock.json").write_text(json.dumps({
                "lockfileVersion": 3,
                "packages": {"node_modules/react": {"version": "18.3.1", "license": "MIT"}},
            }), encoding="utf-8")
            qwen = root / "qwen"
            qwen.mkdir()
            (qwen / "LICENSE").write_text("qwen runtime", encoding="utf-8")
            assets = {}
            components = []
            for component, roles in licenses.REQUIRED_EXTERNAL_ROLES.items():
                artifact = root / f"{component}.bin"
                artifact.write_bytes(component.encode())
                assets[component] = [artifact]
                attachments = []
                for role in roles:
                    material = root / "materials" / component / f"{role}.txt"
                    material.parent.mkdir(parents=True, exist_ok=True)
                    material.write_text(role, encoding="utf-8")
                    attachments.append({
                        "role": role,
                        "path": material.relative_to(root / "materials").as_posix(),
                        "sha256": licenses.sha256(material),
                    })
                components.append({
                    "id": component, "license_expression": "MIT",
                    "source_url": "https://example.invalid/source", "source_revision": "v1",
                    "artifacts": [{"sha256": licenses.sha256(artifact)}],
                    "attachments": attachments,
                })
            supplement = root / "materials/python-demo-LICENSE"
            supplement.write_text("demo", encoding="utf-8")
            components.append({
                "id": "python:demo-1.2", "license_expression": "MIT",
                "source_url": "https://example.invalid/demo", "source_revision": "1.2",
                "attachments": [{"role": "license", "path": supplement.name,
                                 "sha256": licenses.sha256(supplement)}],
            })
            (root / "materials/release-materials.json").write_text(json.dumps({
                "schema_version": 1, "components": components,
            }), encoding="utf-8")
            _, blockers = licenses.build_license_bundle(
                destination=root / "licenses", python_runtime=runtime,
                frontend_root=frontend, qwen_bundle=qwen,
                materials_root=root / "materials", component_assets=assets,
            )
            self.assertFalse(blockers)
            (runtime / "LICENSE_PYTHON.txt").unlink()
            _, blockers = licenses.build_license_bundle(
                destination=root / "licenses-missing-runtime", python_runtime=runtime,
                frontend_root=frontend, qwen_bundle=qwen,
                materials_root=root / "materials", component_assets=assets,
            )
            self.assertIn("Python runtime license text is missing", blockers)

    def test_runtime_smoke_prepares_relocatable_native_paths(self) -> None:
        with patch("os.chdir") as chdir, patch.dict("os.environ", {"PATH": "existing"}, clear=True):
            runtime_smoke.prepare_environment()
            chdir.assert_called_once_with(runtime_smoke.ROOT)
            path_entries = os.environ["PATH"].split(os.pathsep)
            self.assertEqual(path_entries[:3], [
                str(runtime_smoke.ROOT / "runtime"),
                str(runtime_smoke.ROOT / "runtime" / "Scripts"),
                str(runtime_smoke.ROOT / "runtime" / "Library" / "bin"),
            ])
            self.assertEqual(path_entries[-1], "existing")
            self.assertEqual(os.environ["PYTHONDONTWRITEBYTECODE"], "1")


if __name__ == "__main__":
    unittest.main()
