#!/usr/bin/env python3
"""
bootstrap_install.py — minimal intervention installer

- Reads requirements.txt
- Installs any missing packages (by import test, not just pip list)
- Handles common import-name ≠ package-name cases
- If Playwright is present, installs the Chromium browser binary
- Creates necessary directories and .env file

Usage: python bootstrap_install.py
"""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQ_FILES = ["requirements.txt"]  # you can add fallbacks here if needed

# Map package names (as appear in requirements.txt) to the module name used in `import`
IMPORT_NAME_OVERRIDES = {
    "pyyaml": "yaml",
    "python-dotenv": "dotenv",
    "pillow": "PIL",
    # most others import as their package name (click, rich, pydantic, requests, playwright)
}

# Regex to extract a bare package key (left side of version specifiers/extras)
# e.g., "pydantic>=2.0.0", "rich[ansi]>=13", "playwright==1.46.0"
PKG_EXTRACT = re.compile(r"^\s*([A-Za-z0-9_.\-]+)")


def sh(cmd):
    """Run a shell command; return (code, output)."""
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        return 0, out
    except subprocess.CalledProcessError as e:
        return e.returncode, e.output


def import_name_for(package_spec: str) -> str:
    """
    Derive an import test name from a requirement line.
    - Strips version and extras
    - Applies overrides (e.g., pillow -> PIL)
    """
    m = PKG_EXTRACT.match(package_spec)
    pkg_key = (m.group(1) if m else package_spec).lower()

    # strip extras (e.g., rich[ansi] -> rich)
    pkg_key = pkg_key.split("[", 1)[0]

    return IMPORT_NAME_OVERRIDES.get(pkg_key, pkg_key.replace("-", "_"))


def is_installed(import_name: str) -> bool:
    try:
        __import__(import_name)
        return True
    except Exception:
        return False


def pip_install(package_spec: str) -> bool:
    print(f"→ Installing: {package_spec}")
    code, out = sh([sys.executable, "-m", "pip", "install", package_spec])
    if code == 0:
        print(f"✓ Installed: {package_spec}")
        return True
    print(f"✗ FAILED: {package_spec}\n{out}")
    return False


def ensure_requirements():
    req_file = None
    for candidate in REQ_FILES:
        p = ROOT / candidate
        if p.exists():
            req_file = p
            break

    if not req_file:
        print("! No requirements.txt found. Nothing to install.")
        return False, []

    print(f"Using requirements file: {req_file}")
    to_install = []
    with req_file.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            to_install.append(line)
    if not to_install:
        print("! requirements.txt is empty.")
        return True, []

    for spec in to_install:
        import_name = import_name_for(spec)
        if is_installed(import_name):
            print(f"✓ OK: {spec} (import '{import_name}')")
        else:
            pip_install(spec)

    return True, to_install


def ensure_playwright_browsers():
    """Install Playwright browsers if playwright is available"""
    if not is_installed("playwright"):
        return
    print("→ Ensuring Playwright Chromium browser is installed...")
    code, out = sh([sys.executable, "-m", "playwright", "install", "chromium"])
    if code == 0:
        print("✓ Playwright Chromium installed.")
    else:
        print(f"✗ Playwright browsers install failed:\n{out}")


def create_directories():
    """Create necessary project directories"""
    print("→ Creating project directories...")
    directories = [
        ROOT / "datasets",
        ROOT / "storage_state",
        ROOT / "logs",
    ]
    
    for dir_path in directories:
        if not dir_path.exists():
            dir_path.mkdir(parents=True, exist_ok=True)
            print(f"✓ Created: {dir_path.relative_to(ROOT)}/")
        else:
            print(f"✓ Exists: {dir_path.relative_to(ROOT)}/")


def create_env_file():
    """Create .env from .env.example if it doesn't exist"""
    env_example = ROOT / ".env.example"
    env_file = ROOT / ".env"
    
    if not env_example.exists():
        print("! .env.example not found, skipping .env creation")
        return
    
    if env_file.exists():
        print("✓ .env already exists")
        return
    
    print("→ Creating .env from .env.example...")
    try:
        content = env_example.read_text(encoding="utf-8")
        env_file.write_text(content, encoding="utf-8")
        print("✓ Created .env file")
        print("! Remember to update .env with your settings")
    except Exception as e:
        print(f"✗ Failed to create .env: {e}")


def check_python_version():
    """Check if Python version is compatible"""
    required = (3, 8)
    current = sys.version_info[:2]
    
    if current >= required:
        print(f"✓ Python {current[0]}.{current[1]} is compatible (>= 3.8)")
        return True
    else:
        print(f"✗ Python {current[0]}.{current[1]} is too old (required >= 3.8)")
        return False


def main():
    print("╔════════════════════════════════════════════════════════════╗")
    print("║     UI Capture System - Bootstrap Installer               ║")
    print("╚════════════════════════════════════════════════════════════╝\n")
    
    # Check Python version
    if not check_python_version():
        print("\n⚠️  Please upgrade Python to 3.8 or higher")
        sys.exit(1)
    
    # Optional: upgrade pip quietly
    print("→ Upgrading pip...")
    sh([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])
    print()

    # Install requirements
    ok, _ = ensure_requirements()
    print()
    
    # Install Playwright browsers
    ensure_playwright_browsers()
    print()
    
    # Create directories
    create_directories()
    print()
    
    # Create .env file
    create_env_file()
    print()

    if ok:
        print("✅ Bootstrap complete!\n")
        print("Next steps:")
        print("  1. Review and update your .env file")
        print("  2. Set up authentication:")
        print("     python scripts/setup_auth.py")
        print("  3. Run the system:")
        print("     python -m src.cli --help")
        print()
    else:
        print("⚠️  Bootstrap finished with warnings (see above).\n")


if __name__ == "__main__":
    main()