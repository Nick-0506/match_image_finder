import os, sys

def resource_path(rel_path: str) -> str:
    #Return available resource path across dev / PyInstaller (onefile/onedir).
    #Priority:
    # 1. ENV override: DPF_RES_BASE
    # 2. PyInstaller onefile: sys._MEIPASS
    # 3. PyInstaller onedir: dirname(sys.executable)
    # 4. Dev: dirname(__file__)

    # Manual override（For debugging or external resource）
    override = os.environ.get("DPF_RES_BASE")
    if override:
        return os.path.normpath(os.path.join(override, rel_path))

    # PyInstaller
    if getattr(sys, 'frozen', False):
        # PyInstaller
        base = getattr(sys, '_MEIPASS', None)
        if base and os.path.isdir(base):
            return os.path.join(base, rel_path)
        # onedir：exe in Contents/MacOS/（mac）or <dist>/<app>/（win/linux）
        base = os.path.dirname(sys.executable)
        return os.path.join(base, rel_path)
    else:
        # Develop environment：Based on current file instead of CWD
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, rel_path)