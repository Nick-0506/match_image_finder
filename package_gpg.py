import subprocess
import os
import shutil
import re
import platform
import hashlib
import json
import sys
from datetime import datetime

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

APP_SOURCE = "Match_Image_Finder.py"

SUPPORTED_ARCHS = ["x86_64"] if IS_WIN else ["x86_64", "arm64"]
OUTPUT_BASE = "dist"
SIGNER_ID = "snl0506@yahoo.com.tw"  # 替換為你的 GPG 簽章 ID

if len(sys.argv)<2:
    print("Please input version number")
    sys.exit(1)

def normalize_arch(arch):
    if arch.upper() in ("X86_64", "AMD64"):
        return "x86_64"
    return arch.lower()

def write_build_info():
    with open("build_info.py", "w", encoding="utf-8") as f:
        f.write(f'VERSION = "{sys.argv[1]}"\n')
        f.write(f'BUILD_TIME = "{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}"\n')

def calculate_sha256(file_path):
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()

def create_build_json(arch, version, binary_path):
    build_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    sha256 = calculate_sha256(binary_path)
    platform = "macos" if IS_MAC else "windows"
    builds = {
        "build_id": build_id,
        "version": version,
        "arch": arch,
        "binary_path": os.path.basename(binary_path),
        "sha256": sha256,
        "timestamp": datetime.now().isoformat()
    }
    json_name = f"builds_v{version}_{platform}_{arch}.json"
    with open(json_name, "w") as f:
        json.dump(builds, f, indent=2)
    print(f"📄 Created {json_name}")
    return json_name

def sign_json(json_path):
    asc_path = json_path + ".asc"
    cmd = ["gpg", "--armor", "--detach-sign", "--local-user", SIGNER_ID, json_path]
    try:
        subprocess.run(cmd, check=True)
        print(f"🔏 Signed → {asc_path}")
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to sign {json_path}: {e}")

def run_pyinstaller(arch, version):
    print(f"📦 Building for {arch}...")
    write_build_info()
    
    # Clear folder
    for d in ("build", f"{arch}.spec"):
        if os.path.exists(d):
            if os.path.isdir(d):
                shutil.rmtree(d)
            else:
                os.remove(d)

    if IS_MAC:
        ICON_FILE = "assets/app.icns"
        pyinstaller_cmd = f"pyinstaller --noconfirm --windowed --onedir --icon={ICON_FILE} " \
                        f"--add-data 'i18n/*.json:i18n' --add-data 'assets/app.icns:assets' " \
                        f"--add-data 'icons/*.png:icons' " \
                        f"--collect-all pgpy --copy-metadata pgpy --copy-metadata cryptography " \
                        f"--collect-all rawpy --copy-metadata rawpy --collect-all pillow_heif --copy-metadata pillow_heif " \
                        f"--copy-metadata PyQt5 --copy-metadata Pillow --copy-metadata imagehash --exclude-module torch --exclude-module numba {APP_SOURCE}"

        dist_app_name = os.path.splitext(APP_SOURCE)[0] + ".app"
        dist_app_dir = os.path.join("dist", dist_app_name)

        if os.path.exists(dist_app_dir):
            print(f"🧹 Removing previous .app: {dist_app_dir}")
            shutil.rmtree(dist_app_dir)

        # Run PyInstaller command
        subprocess.run(pyinstaller_cmd, shell=True, check=True)

        # Rename Match_Image_Finder_vx.x.x.app to Match_Image_Finder_vx.x.x_macos_x86_64.app
        versioned_output_dir = os.path.join(f"{OUTPUT_BASE}_macos_{arch}", f"{os.path.splitext(APP_SOURCE)[0]}_v{version}.app")
        if os.path.exists(f"{OUTPUT_BASE}_macos_{arch}"):
            shutil.rmtree(f"{OUTPUT_BASE}_macos_{arch}")
        os.makedirs(f"{OUTPUT_BASE}_macos_{arch}", exist_ok=True)
        shutil.move(dist_app_dir, versioned_output_dir)

        binary_path = os.path.join(versioned_output_dir, "Contents", "MacOS", os.path.splitext(APP_SOURCE)[0])

    elif IS_WIN:
        ICON_FILE = "assets/app.ico"
        pyinstaller_cmd = [sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--windowed", "--onefile", f"--icon={ICON_FILE}", "--add-data", r"i18n\*.json;i18n", "--add-data", r"assets\app.ico;assets",
            "--add-data", r"icons\*.png;icons",
            "--collect-all", "pgpy", "--copy-metadata", "pgpy", "--copy-metadata", "cryptography",
            "--collect-all", "rawpy", "--copy-metadata", "rawpy", "--collect-all", "pillow_heif", "--copy-metadata", "pillow_heif",
            "--copy-metadata", "PyQt5", "--copy-metadata", "Pillow", "--copy-metadata", "imagehash",
            "--exclude-module", "torch", "--exclude-module", "numba", APP_SOURCE
        ]

        # Run PyInstaller command
        subprocess.run(pyinstaller_cmd, check=True)

        # Get binary path
        binary_path = os.path.join("dist", os.path.splitext(APP_SOURCE)[0] + ".exe")

        # Rename Match_Image_Finder_vx.x.x.exe to Match_Image_Finder_vx.x.x_windows_x86_64.exe
        versioned_output_dir = os.path.join(f"{OUTPUT_BASE}_windows_{arch}", f"{os.path.splitext(APP_SOURCE)[0]}_v{version}.exe")
        if os.path.exists(f"{OUTPUT_BASE}_windows_{arch}"):
            shutil.rmtree(f"{OUTPUT_BASE}_windows_{arch}")
        os.makedirs(f"{OUTPUT_BASE}_windows_{arch}", exist_ok=True)
        shutil.move(binary_path, versioned_output_dir)
        
        # Windows binary_path is versioned_output_dir
        binary_path = versioned_output_dir

    print(f"✅ {arch} build done → {versioned_output_dir}")
    
    if not os.path.exists(binary_path):
        print(f"❌ Can't find executiable file：{binary_path}")
        return

    # 🔁 Create hash + json + signature
    json_file = create_build_json(arch, version, binary_path)
    sign_json(json_file)

if __name__ == "__main__":

    machine = normalize_arch(platform.machine())
    print(f"🖥️  Detected host architecture: {machine}")

    for arch in SUPPORTED_ARCHS:
        if machine != arch:
            print(f"⚠️  Skipping unsupported arch: {arch} (current: {machine})")
            continue
        try:
            run_pyinstaller(arch, sys.argv[1])
        except subprocess.CalledProcessError as e:
            print(f"❌ Build failed for {arch}: {e}")
            continue

    print("🎉 Build process complete.")