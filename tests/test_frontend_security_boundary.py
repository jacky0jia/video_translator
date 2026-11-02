import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "app" / "frontend"


def test_tailwind_remains_version_three_and_build_only():
    package = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    assert package["devDependencies"]["tailwindcss"].startswith("^3.")
    assert "tailwindcss" not in package["dependencies"]
    assert "@tailwindcss/vite" not in package["devDependencies"]
    assert (FRONTEND / "postcss.config.js").is_file()
    assert "@tailwind base;" in (FRONTEND / "src" / "index.css").read_text(encoding="utf-8")


def test_accepted_vulnerable_chain_never_enters_production_dependencies():
    lock = json.loads((FRONTEND / "package-lock.json").read_text(encoding="utf-8"))
    affected = {"tailwindcss", "braces", "postcss-selector-parser", "postcss-nested",
                "chokidar", "micromatch", "fast-glob"}
    checked = set()
    for path, package in lock["packages"].items():
        name = path.rsplit("node_modules/", 1)[-1]
        if name in affected:
            assert package.get("dev") is True, f"Accepted build-only risk entered runtime: {path}"
            checked.add(name)
    assert {"tailwindcss", "braces", "postcss-selector-parser"} <= checked


def test_tailwind_sources_remain_confined_to_reviewed_frontend_files():
    config = (FRONTEND / "tailwind.config.js").read_text(encoding="utf-8")
    content = re.search(r"content:\s*\[(.*?)\]", config, re.S).group(1)
    assert re.findall(r'[\"\']([^\"\']+)[\"\']', content) == [
        "./index.html", "./src/**/*.{js,ts,jsx,tsx}"
    ]
    assert "plugins: []" in config


def test_source_map_js_uses_compatible_patched_version():
    lock = json.loads((FRONTEND / "package-lock.json").read_text(encoding="utf-8"))
    version = tuple(map(int, lock["packages"]["node_modules/source-map-js"]["version"].split(".")))
    assert version[0] == 1
    assert version >= (1, 2, 2)
