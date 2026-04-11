import ctypes
from enum import Enum
import os
import site
from typing import Dict, List
from .mlogging import Logger

logger = Logger.build("RuntimeTool")

class Libs:
    CUBLAS = "nvidia/cublas/lib/libcublas.so.12"
    CUDNN = "nvidia/cudnn/lib/libcudnn.so.8"

def inject_libs(libs: List[str]):
    try:
        site_packages = site.getsitepackages()[0]
        for lib in libs:
            lib_path = os.path.join(site_packages, lib)
            ctypes.CDLL(lib_path, mode=ctypes.RTLD_GLOBAL)
    except Exception as e:
        logger.error(f"Inject libs failed: {e}")

class Envs:
    HF_ENDPOINT = ("HF_ENDPOINT", "https://hf-mirror.com")

def inject_envs(envs: Dict[str, str]):
    os.environ.update(envs)