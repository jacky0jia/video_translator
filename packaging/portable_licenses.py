"""Collect portable license texts and validate public-release materials."""

from __future__ import annotations

import csv
from email.parser import Parser
import hashlib
import json
from pathlib import Path
import re
import shutil


LICENSE_NAME = re.compile(
    r"^(license|licence|copying|copyright|notice|authors?)([._-].*)?$",
    re.IGNORECASE,
)
REQUIRED_EXTERNAL_ROLES = {
    "ffmpeg": {"license", "build_info", "corresponding_source"},
    "asr-model": {"license", "model_card"},
    "kokoro-model": {"license", "model_card"},
    "qwen-model": {"license", "model_card"},
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", value.lower()).strip("-") or "unknown"


def _is_license_path(path: Path) -> bool:
    return any(LICENSE_NAME.match(part) for part in path.parts)


def _copy_unique(source: Path, destination: Path, relative: Path, *, report_root: Path) -> dict:
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and sha256(target) != sha256(source):
        target = target.with_name(f"{target.stem}-{sha256(source)[:8]}{target.suffix}")
    shutil.copy2(source, target)
    return {
        "path": target.relative_to(report_root).as_posix(),
        "bytes": target.stat().st_size,
        "sha256": sha256(target),
    }


def collect_python_licenses(python_runtime: Path, licenses_root: Path) -> tuple[dict, list[str]]:
    """Copy license files owned by every installed Python distribution."""
    site_packages = python_runtime / "Lib" / "site-packages"
    destination = licenses_root / "python"
    packages: list[dict] = []
    blockers: list[str] = []
    metadata_directories = sorted(
        [*site_packages.glob("*.dist-info"), *site_packages.glob("*.egg-info")]
    )
    for dist_info in metadata_directories:
        legacy = dist_info.name.endswith(".egg-info")
        metadata_path = dist_info / ("PKG-INFO" if legacy else "METADATA")
        metadata = Parser().parsestr(
            metadata_path.read_text(encoding="utf-8", errors="replace")
            if metadata_path.is_file() else ""
        )
        name = metadata.get("Name") or dist_info.name.removesuffix(".egg-info" if legacy else ".dist-info")
        version = metadata.get("Version") or "unknown"
        component = f"{_safe_name(name)}-{_safe_name(version)}"
        candidates = {
            path.resolve()
            for path in dist_info.rglob("*")
            if path.is_file() and _is_license_path(path.relative_to(dist_info))
        }
        record_path = dist_info / "RECORD"
        if record_path.is_file():
            with record_path.open("r", encoding="utf-8", errors="replace", newline="") as stream:
                for row in csv.reader(stream):
                    if not row:
                        continue
                    relative = Path(row[0].replace("/", "\\"))
                    if not _is_license_path(relative):
                        continue
                    candidate = (site_packages / relative).resolve()
                    try:
                        candidate.relative_to(python_runtime.resolve())
                    except ValueError:
                        continue
                    if candidate.is_file():
                        candidates.add(candidate)
        attachments = []
        for candidate in sorted(candidates):
            try:
                relative = candidate.relative_to(site_packages.resolve())
            except ValueError:
                relative = Path(candidate.name)
            attachments.append(_copy_unique(
                candidate, destination, Path(component) / relative, report_root=licenses_root
            ))
        license_expression = metadata.get("License-Expression") or metadata.get("License") or ""
        classifiers = metadata.get_all("Classifier") or []
        packages.append({
            "name": name,
            "version": version,
            "license_expression": license_expression.strip(),
            "license_classifiers": [item for item in classifiers if item.startswith("License ::")],
            "attachments": attachments,
        })
        if not attachments:
            blockers.append(f"Python distribution has no attached license text: {name} {version}")

    runtime_attachments = []
    for name in ("LICENSE_PYTHON.txt", "LICENSE.txt", "LICENSE"):
        path = python_runtime / name
        if path.is_file():
            runtime_attachments.append(
                _copy_unique(
                    path, licenses_root / "python-runtime", Path(path.name),
                    report_root=licenses_root,
                )
            )
    if not runtime_attachments:
        blockers.append("Python runtime license text is missing")
    return {
        "runtime_attachments": runtime_attachments,
        "packages": packages,
    }, blockers


def collect_frontend_licenses(frontend_root: Path, licenses_root: Path) -> tuple[dict, list[str]]:
    """Collect production dependency licenses from the exact npm lockfile."""
    lock_path = frontend_root / "package-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    packages = []
    blockers = []
    destination = licenses_root / "frontend"
    for package_path, metadata in sorted(lock.get("packages", {}).items()):
        if not package_path.startswith("node_modules/") or metadata.get("dev") is True:
            continue
        source = frontend_root / Path(package_path)
        name = metadata.get("name") or package_path.rsplit("node_modules/", 1)[-1]
        version = metadata.get("version") or "unknown"
        attachments = []
        if source.is_dir():
            for candidate in sorted(source.iterdir()):
                if candidate.is_file() and LICENSE_NAME.match(candidate.name):
                    attachments.append(_copy_unique(
                        candidate, destination,
                        Path(f"{_safe_name(name)}-{_safe_name(version)}") / candidate.name,
                        report_root=licenses_root,
                    ))
        packages.append({
            "name": name,
            "version": version,
            "license_expression": metadata.get("license", ""),
            "attachments": attachments,
        })
        if not attachments:
            blockers.append(f"Frontend production dependency has no attached license text: {name} {version}")
    return {"lockfile_version": lock.get("lockfileVersion"), "packages": packages}, blockers


def collect_embedded_licenses(
    source: Path, destination: Path, *, report_root: Path
) -> list[dict]:
    attachments = []
    for candidate in sorted(source.rglob("*")):
        if candidate.is_file() and _is_license_path(candidate.relative_to(source)):
            attachments.append(_copy_unique(
                candidate, destination, candidate.relative_to(source), report_root=report_root
            ))
    return attachments


def _resolve_material_path(root: Path, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Release material path must be relative and contained: {value}")
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Release material escapes its directory: {value}") from exc
    if not path.is_file():
        raise ValueError(f"Release material is missing: {value}")
    return path


def collect_external_materials(
    materials_root: Path | None,
    licenses_root: Path,
    component_assets: dict[str, list[Path]],
) -> tuple[dict, list[str]]:
    if materials_root is None:
        return {"components": []}, [
            f"External release materials are missing for {component}"
            for component in component_assets
        ]
    manifest_path = materials_root / "release-materials.json"
    if not manifest_path.is_file():
        raise ValueError(f"Release materials manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported release materials schema")
    components = {item.get("id"): item for item in manifest.get("components", [])}
    blockers = []
    output = []
    for component, required_roles in REQUIRED_EXTERNAL_ROLES.items():
        if component not in component_assets:
            continue
        entry = components.get(component)
        if not isinstance(entry, dict):
            blockers.append(f"External release materials are missing for {component}")
            continue
        for field in ("license_expression", "source_url", "source_revision"):
            if not str(entry.get(field, "")).strip():
                blockers.append(f"{component} release material lacks {field}")
        assets = component_assets[component]
        if not assets:
            blockers.append(f"{component} has no packaged assets")
        actual_hashes = sorted(sha256(path) for path in assets)
        declared_hashes = sorted(str(item.get("sha256", "")) for item in entry.get("artifacts", []))
        if actual_hashes != declared_hashes:
            blockers.append(f"{component} artifact hashes do not match the packaged assets")
        attachments = []
        roles = set()
        for item in entry.get("attachments", []):
            role = str(item.get("role", ""))
            roles.add(role)
            source = _resolve_material_path(materials_root, str(item.get("path", "")))
            expected = str(item.get("sha256", ""))
            if sha256(source) != expected:
                raise ValueError(f"Release material checksum mismatch: {item.get('path')}")
            relative = Path(str(item["path"]))
            copied = _copy_unique(
                source, licenses_root / "external" / component, relative,
                report_root=licenses_root,
            )
            copied["role"] = role
            attachments.append(copied)
        missing_roles = sorted(required_roles - roles)
        if missing_roles:
            blockers.append(f"{component} release material lacks roles: {', '.join(missing_roles)}")
        output.append({
            "id": component,
            "license_expression": entry.get("license_expression"),
            "source_url": entry.get("source_url"),
            "source_revision": entry.get("source_revision"),
            "artifact_sha256": actual_hashes,
            "attachments": attachments,
        })
    for component, entry in sorted(components.items()):
        if component in REQUIRED_EXTERNAL_ROLES or not isinstance(entry, dict):
            continue
        if not (component.startswith("python:") or component.startswith("frontend:")
                or component == "qwen-runtime"):
            blockers.append(f"Unsupported supplemental release material: {component}")
            continue
        for field in ("license_expression", "source_url", "source_revision"):
            if not str(entry.get(field, "")).strip():
                blockers.append(f"{component} release material lacks {field}")
        attachments = []
        roles = set()
        for item in entry.get("attachments", []):
            role = str(item.get("role", ""))
            roles.add(role)
            source = _resolve_material_path(materials_root, str(item.get("path", "")))
            if sha256(source) != str(item.get("sha256", "")):
                raise ValueError(f"Release material checksum mismatch: {item.get('path')}")
            copied = _copy_unique(
                source, licenses_root / "external" / _safe_name(component),
                Path(str(item["path"])),
                report_root=licenses_root,
            )
            copied["role"] = role
            attachments.append(copied)
        if "license" not in roles:
            blockers.append(f"{component} release material lacks roles: license")
        output.append({
            "id": component,
            "license_expression": entry.get("license_expression"),
            "source_url": entry.get("source_url"),
            "source_revision": entry.get("source_revision"),
            "artifact_sha256": [],
            "attachments": attachments,
        })
    return {"components": output}, blockers


def build_license_bundle(
    *, destination: Path, python_runtime: Path, frontend_root: Path,
    qwen_bundle: Path | None, materials_root: Path | None,
    component_assets: dict[str, list[Path]],
) -> tuple[dict, list[str]]:
    destination.mkdir(parents=True, exist_ok=True)
    python, _ = collect_python_licenses(python_runtime, destination)
    frontend, _ = collect_frontend_licenses(frontend_root, destination)
    qwen_runtime = collect_embedded_licenses(
        qwen_bundle, destination / "qwen-runtime", report_root=destination
    ) if qwen_bundle is not None else []
    external, external_blockers = collect_external_materials(
        materials_root, destination, component_assets
    )
    supplemental_ids = {item["id"] for item in external["components"]}
    python_blockers = [
        f"Python distribution has no attached license text: {item['name']} {item['version']} "
        f"(supply python:{_safe_name(item['name'])}-{_safe_name(item['version'])})"
        for item in python["packages"]
        if not item["attachments"]
        and f"python:{_safe_name(item['name'])}-{_safe_name(item['version'])}" not in supplemental_ids
    ]
    if not python["packages"]:
        python_blockers.append("No Python distributions were discovered in the portable runtime")
    if not python["runtime_attachments"]:
        python_blockers.append("Python runtime license text is missing")
    frontend_blockers = [
        f"Frontend production dependency has no attached license text: {item['name']} {item['version']} "
        f"(supply frontend:{_safe_name(item['name'])}-{_safe_name(item['version'])})"
        for item in frontend["packages"]
        if not item["attachments"]
        and f"frontend:{_safe_name(item['name'])}-{_safe_name(item['version'])}" not in supplemental_ids
    ]
    qwen_blockers = (
        [] if qwen_bundle is None or qwen_runtime or "qwen-runtime" in supplemental_ids
        else ["Qwen runtime has no embedded license attachments (supply qwen-runtime)"]
    )
    blockers = python_blockers + frontend_blockers + qwen_blockers + external_blockers
    manifest = {
        "schema_version": 1,
        "python": python,
        "frontend": frontend,
        "qwen_runtime_attachments": qwen_runtime,
        "external": external,
        "public_distribution_blockers": blockers,
    }
    (destination / "THIRD-PARTY-MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest, blockers
