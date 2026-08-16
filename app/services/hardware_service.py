import logging
import platform
import shutil
import subprocess
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Estimated VRAM requirements (GB) for faster-whisper models
VRAM_TABLE = {
    "tiny": {"float16": 1.0, "int8": 0.5},
    "base": {"float16": 1.5, "int8": 0.8},
    "small": {"float16": 2.5, "int8": 1.5},
    "medium": {"float16": 5.0, "int8": 2.5},
    "large-v3": {"float16": 10.0, "int8": 5.0},
}


class HardwareService:
    def __init__(self):
        self.nvidia_info: Optional[Dict] = self._detect_nvidia()
        self.amd_info: Optional[Dict] = self._detect_amd()

    def _detect_nvidia(self) -> Optional[Dict]:
        """Detect NVIDIA GPU and query free VRAM."""
        # Try pynvml first
        try:
            import pynvml
            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            name = pynvml.nvmlDeviceGetName(handle)
            # pynvml >= 11 returns bytes; decode if necessary
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="replace")
            return {
                "vendor": "NVIDIA",
                "name": name,
                "total_gb": mem_info.total / (1024 ** 3),
                "free_gb": mem_info.free / (1024 ** 3),
            }
        except Exception as e:
            logger.info(f"pynvml detection failed: {e}")

        # Fallback to torch.cuda
        try:
            import torch
            if torch.cuda.is_available():
                idx = torch.cuda.current_device()
                name = torch.cuda.get_device_name(idx)
                total = torch.cuda.get_device_properties(idx).total_memory
                allocated = torch.cuda.memory_allocated(idx)
                free = total - allocated
                return {
                    "vendor": "NVIDIA",
                    "name": name,
                    "total_gb": total / (1024 ** 3),
                    "free_gb": free / (1024 ** 3),
                }
        except Exception as e:
            logger.info(f"torch.cuda detection failed: {e}")

        # Fallback to ctranslate2 (used by faster-whisper, always present)
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0:
                return {
                    "vendor": "NVIDIA",
                    "name": "NVIDIA GPU (via ctranslate2)",
                    "total_gb": 0.0,
                    "free_gb": 0.0,
                }
        except Exception as e:
            logger.info(f"ctranslate2 detection failed: {e}")

        return None

    def _detect_amd(self) -> Optional[Dict]:
        """Detect AMD GPU via platform-specific methods."""
        if platform.system() == "Windows":
            return self._detect_amd_windows()
        if platform.system() == "Linux":
            return self._detect_amd_linux()
        return self._detect_amd_vulkan()

    def _detect_amd_windows(self) -> Optional[Dict]:
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-Command",
                    "Get-WmiObject Win32_VideoController | Select-Object Name,AdapterRAM | Format-List",
                ],
                capture_output=True,
                text=True,
            )
            current_name = None
            for line in result.stdout.splitlines():
                if line.strip().startswith("Name"):
                    current_name = line.split(":", 1)[-1].strip()
                if line.strip().startswith("AdapterRAM") and current_name and "AMD" in current_name.upper():
                    ram = int(line.split(":", 1)[-1].strip())
                    return {
                        "vendor": "AMD",
                        "name": current_name,
                        "total_gb": ram / (1024 ** 3),
                        "free_gb": ram / (1024 ** 3),
                    }
        except Exception:
            pass
        return None

    def _detect_amd_linux(self) -> Optional[Dict]:
        try:
            import glob
            for mem_file in glob.glob("/sys/class/drm/card*/device/mem_info_vram_total"):
                vendor_file = mem_file.replace("mem_info_vram_total", "vendor")
                with open(vendor_file) as f:
                    vendor = f.read().strip()
                if vendor == "0x1002":  # AMD PCI vendor ID
                    with open(mem_file) as f:
                        total_bytes = int(f.read().strip())
                    name = "AMD GPU"
                    try:
                        prod_file = mem_file.replace("mem_info_vram_total", "product_name")
                        with open(prod_file) as f:
                            name = f.read().strip()
                    except Exception:
                        pass
                    return {
                        "vendor": "AMD",
                        "name": name,
                        "total_gb": total_bytes / (1024 ** 3),
                        "free_gb": total_bytes / (1024 ** 3),
                    }
        except Exception:
            pass
        return None

    def _detect_amd_vulkan(self) -> Optional[Dict]:
        vulkaninfo = shutil.which("vulkaninfo")
        if not vulkaninfo:
            return None
        try:
            result = subprocess.run(
                [vulkaninfo, "--summary"],
                capture_output=True,
                text=True,
            )
            lines = result.stdout.splitlines()
            device_name = None
            heap_size_bytes = 0
            for line in lines:
                lower = line.lower()
                if "deviceName" in line and not device_name:
                    device_name = line.split("=")[-1].strip()
                if "size" in lower and "(" in line:
                    try:
                        # Parse "size = 17179869184 (0x400000000) (16.00 GiB)"
                        size_part = line.split("=")[-1].strip()
                        size_num = int(size_part.split()[0])
                        if size_num > heap_size_bytes:
                            heap_size_bytes = size_num
                    except (ValueError, IndexError):
                        pass
            if device_name and heap_size_bytes > 0 and "AMD" in device_name.upper():
                return {
                    "vendor": "AMD",
                    "name": device_name,
                    "total_gb": heap_size_bytes / (1024 ** 3),
                    "free_gb": heap_size_bytes / (1024 ** 3),
                }
        except Exception:
            pass
        return None

    def recommend_device(self, model_size: str, compute_type: str) -> Tuple[str, str, str]:
        """
        Returns (device_for_faster_whisper, compute_type, reason).
        faster-whisper device strings: "cuda" or "cpu".
        """
        req_gb = VRAM_TABLE.get(model_size, {}).get(compute_type, 999)

        if self.nvidia_info:
            free_gb = self.nvidia_info["free_gb"]
            # If VRAM info is unavailable (e.g. detected via ctranslate2), skip check
            if free_gb == 0.0 or free_gb >= req_gb:
                return (
                    "cuda",
                    compute_type,
                    f"NVIDIA {self.nvidia_info['name']}: {free_gb:.1f}GB free >= {req_gb}GB required" if free_gb > 0 else f"NVIDIA {self.nvidia_info['name']}: GPU detected",
                )
            else:
                return (
                    "cpu",
                    "int8",
                    f"NVIDIA {self.nvidia_info['name']}: {free_gb:.1f}GB free < {req_gb}GB required; falling back to CPU",
                )

        if self.amd_info:
            total_gb = self.amd_info["total_gb"]
            # AMD on Windows/Linux without ROCm: faster-whisper runs on CPU
            return (
                "cpu",
                "int8",
                f"AMD {self.amd_info['name']} detected ({total_gb:.1f}GB). "
                f"ASR uses CPU backend on this platform (ROCm disabled).",
            )

        return ("cpu", "int8", "No compatible GPU detected; using CPU.")

    def get_summary(self) -> Dict:
        gpus = []
        if self.nvidia_info:
            gpus.append(self.nvidia_info)
        if self.amd_info:
            gpus.append(self.amd_info)
        return {
            "gpus": gpus,
            "recommendation": self.recommend_device("base", "float16") if gpus else None,
        }


hardware_service = HardwareService()
