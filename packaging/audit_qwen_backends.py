"""Read-only inventory of pinned AMD backend candidates; never installs or runs them."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import zipfile


RELEASE = "https://github.com/ggml-org/llama.cpp/releases/tag/b10792"
CANDIDATES = {
    "vulkan": {
        "filename": "llama-b10792-bin-win-vulkan-x64.zip",
        "sha256": "c55e5ce547153d21ff2ab4f19fada11e808f7372db8c57f69ca721d5976b2613",
        "backend_files": ("ggml-vulkan.dll",),
    },
    "hip": {
        "filename": "llama-b10792-bin-win-rocm-10.0-x64.zip",
        "sha256": "822cc805833e2f3277b0b8482f79453a6bc8d49f311f9c5856a276ec2c368f3f",
        "backend_files": ("ggml-hip.dll", "amdhip64_7.dll", "amd_comgr.dll", "rocm_kpack.dll"),
    },
}
COMMON_FILES = (
    "llama-tts.exe", "llama.dll", "llama-common.dll", "mtmd.dll",
    "ggml.dll", "ggml-base.dll", "ggml-cpu-x64.dll", "libomp.dll", "LICENSE-LLVM-OpenMP",
)


class CandidateAuditError(ValueError):
    pass


def audit_candidate(archive_path: str | Path, backend: str) -> dict:
    if backend not in CANDIDATES:
        raise CandidateAuditError("Unknown candidate backend")
    candidate = CANDIDATES[backend]
    inventory = []
    # Keep a single file handle for checksum and ZIP inspection.
    with Path(archive_path).open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != candidate["sha256"]:
            raise CandidateAuditError("Candidate archive checksum mismatch")
        source.seek(0)
        try:
            with zipfile.ZipFile(source) as archive:
                entries = archive.infolist()
                if len(entries) > 512 or sum(item.file_size for item in entries) > 2 * 1024**3:
                    raise CandidateAuditError("Candidate archive exceeds inventory limits")
                names = set()
                for item in entries:
                    name = item.filename
                    if (not name or name in {".", ".."} or any(c in name for c in "/\\:")
                            or name.rstrip(". ") != name or item.is_dir()
                            or stat.S_ISLNK(item.external_attr >> 16)):
                        raise CandidateAuditError("Candidate must contain direct regular files")
                    if name.casefold() in names:
                        raise CandidateAuditError("Duplicate candidate filename")
                    names.add(name.casefold())
                    with archive.open(item) as content:
                        file_hash = hashlib.file_digest(content, "sha256").hexdigest()
                    inventory.append({"name": name, "bytes": item.file_size, "sha256": file_hash})
                required = {name.casefold() for name in COMMON_FILES + candidate["backend_files"]}
                missing = required - names
                if missing:
                    raise CandidateAuditError(f"Missing candidate files: {', '.join(sorted(missing))}")
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
            raise CandidateAuditError("Candidate ZIP is unreadable") from exc
    return {
        "backend": backend,
        "runtime_version": "b10792-c5a5535e6",
        "release_url": RELEASE,
        "archive": candidate["filename"],
        "archive_sha256": digest,
        "audit_status": "inventory_verified",
        "amd_hardware_validated": False,
        "production_ready": False,
        "uncompressed_bytes": sum(item["bytes"] for item in inventory),
        "files": sorted(inventory, key=lambda item: item["name"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archives-dir", required=True, type=Path)
    parser.add_argument("--backend", required=True, choices=tuple(CANDIDATES))
    args = parser.parse_args()
    try:
        report = audit_candidate(args.archives_dir / CANDIDATES[args.backend]["filename"], args.backend)
    except (CandidateAuditError, OSError) as exc:
        parser.exit(1, f"Candidate audit failed: {exc}\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
