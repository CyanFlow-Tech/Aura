import ctypes
import importlib.util
import os
from pathlib import Path
from typing import Dict, List, NamedTuple

from .mlogging import Logger

logger = Logger.build("RuntimeTool")


class LibSpec(NamedTuple):
    """Describes one native library shipped inside a Python package wheel.

    - `package`: Python module name whose install directory contains the lib
      (e.g. ``"nvidia.cublas"`` for ``nvidia-cublas-cu12``).
    - `subdir`: subdirectory under the package root, typically ``"lib"``.
    - `pattern`: glob pattern for the .so file(s), version wildcards allowed
      (e.g. ``"libcudnn.so.*"`` tolerates cuDNN 8/9/10 without code changes).
    """

    package: str
    subdir: str
    pattern: str


class Libs:
    CUBLAS = LibSpec("nvidia.cublas", "lib", "libcublas.so.*")
    CUDNN = LibSpec("nvidia.cudnn", "lib", "libcudnn.so.[0-9]*")


def _resolve_package_dir(module: str) -> Path | None:
    spec = importlib.util.find_spec(module)
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(next(iter(spec.submodule_search_locations)))


def inject_libs(libs: List[LibSpec]) -> None:
    """Preload native libraries with RTLD_GLOBAL so downstream C extensions
    (e.g. ctranslate2) can dlopen them without LD_LIBRARY_PATH.

    Both libcublas.so.12 and libcudnn.so.9 have ``RUNPATH=$ORIGIN``, so once
    the main .so is loaded its sibling deps (libcublasLt.so.12,
    libcudnn_ops.so.9, ...) resolve automatically from the same directory.
    """
    for lib in libs:
        pkg_dir = _resolve_package_dir(lib.package)
        if pkg_dir is None:
            logger.error(f"package not found: {lib.package}")
            continue
        lib_dir = pkg_dir / lib.subdir
        matches = sorted(lib_dir.glob(lib.pattern))
        if not matches:
            logger.error(f"no lib matched {lib.pattern} in {lib_dir}")
            continue
        for path in matches:
            try:
                ctypes.CDLL(str(path), mode=ctypes.RTLD_GLOBAL)
                logger.info(f"loaded {path.name}")
            except OSError as e:
                logger.error(f"load {path} failed: {e}")


class Envs:
    HF_ENDPOINT = ("HF_ENDPOINT", "https://hf-mirror.com")


def inject_envs(envs: Dict[str, str]) -> None:
    os.environ.update(envs)
