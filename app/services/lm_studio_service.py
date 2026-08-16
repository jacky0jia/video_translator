"""LM Studio CLI integration for local model lifecycle management."""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path


class LMStudioService:
    def __init__(self, cli_path: str = "lms", port: int = 1234):
        resolved = shutil.which(cli_path) if not Path(cli_path).exists() else cli_path
        self.cli_path = str(resolved or cli_path)
        self.port = int(port)

    def _run(self, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            result = subprocess.run(
                [self.cli_path, *args], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
                creationflags=creationflags,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "LM Studio CLI 'lms' was not found. Install LM Studio and run it once, "
                "or configure LM_STUDIO_CLI_PATH."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"LM Studio CLI timed out: lms {' '.join(args)}") from exc
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "unknown error").strip()
            raise RuntimeError(f"LM Studio CLI failed: {message}")
        return result

    def server_status(self) -> dict:
        result = self._run("server", "status", "--json", timeout=15)
        try:
            return json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError("LM Studio returned an invalid server status") from exc

    def ensure_server(self) -> None:
        status = self.server_status()
        if status.get("running"):
            return
        self._run("server", "start", "--port", str(self.port), timeout=30)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.server_status().get("running"):
                return
            time.sleep(0.5)
        raise RuntimeError("LM Studio server did not start within 20 seconds")

    def list_models(self) -> list[str]:
        result = self._run("ls", "--llm", "--json", timeout=30)
        try:
            data = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise RuntimeError("LM Studio returned an invalid model list") from exc
        if not isinstance(data, list):
            return []
        return [
            str(item.get("modelKey"))
            for item in data
            if isinstance(item, dict) and item.get("modelKey")
        ]

    def list_runtimes(self) -> list[str]:
        """Return installed inference runtime descriptions reported by lms."""
        result = self._run("runtime", "ls", timeout=30)
        output = (result.stdout or result.stderr or "").strip()
        if not output or "No runtimes found" in output:
            return []
        return [line.strip() for line in output.splitlines() if line.strip()]

    def load_model(self, model: str, ttl_seconds: int = 300) -> None:
        if not model:
            raise RuntimeError(
                "No LM Studio model is selected. Choose one in Settings > Services & diagnostics."
            )
        if not self.list_runtimes():
            raise RuntimeError(
                "LM Studio has no inference runtime installed. Update LM Studio, then open "
                "its Runtime page (Ctrl+Shift+R on Windows) and install/select a GGUF "
                "llama.cpp runtime. Retry translation after the runtime is ready."
            )
        try:
            self._run(
                "load", model, "--ttl", str(max(int(ttl_seconds), 1)), "--yes",
                timeout=180,
            )
        except RuntimeError as exc:
            if "No LM Runtime found for model format" in str(exc):
                raise RuntimeError(
                    "LM Studio cannot find a GGUF inference runtime. Update LM Studio, then "
                    "open its Runtime page (Ctrl+Shift+R on Windows) and install/select a "
                    "compatible llama.cpp runtime before retrying translation."
                ) from exc
            raise

    def resolve_model(self, configured_model: str = "") -> str:
        if configured_model:
            return configured_model
        models = self.list_models()
        if not models:
            raise RuntimeError(
                "No LM Studio LLM is installed. Download one with 'lms get <model>' "
                "or from LM Studio, then select it in Settings."
            )
        return models[0]

    def unload_model(self, model: str) -> None:
        if model:
            self._run("unload", model, timeout=60)
