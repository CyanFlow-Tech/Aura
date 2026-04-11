import os
from contextlib import contextmanager
from pathlib import Path
from utils.mlogging import LoggingMixin

class LRUFileCache(LoggingMixin):

    def __init__(self, cache_dir: str, max_files: int = 10):
        super().__init__()
        self.cache_dir = Path(cache_dir)
        self.num_files_limit = max_files
        self.num_files_trigger = max_files * 2
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def evict(self, path: str, mode: str = "wb"):
        if mode in ('r', 'rb'): 
            return
        ext = os.path.basename(path).split(".")[-1]
        files = list(self.cache_dir.glob(f"*.{ext}"))
        if len(files) <= self.num_files_trigger:
            return
        files.sort(key=os.path.getmtime)
        files_to_delete = len(files) - self.num_files_limit

        for i in range(files_to_delete):
            try:
                os.remove(files[i])
            except Exception as e:
                self.logger.error(f"Failed to remove file {files[i]}: {e}")
    
    @contextmanager
    def open(self, path: str, mode: str = "rb"):
        with open(self.cache_dir / path, mode) as f: yield f
        self.evict(path, mode)

    def path(self, path: str):
        return str(self.cache_dir / path)
        
