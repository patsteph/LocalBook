"""Update checking API endpoints"""
import httpx
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from pathlib import Path
import json
import subprocess
import os
import sys
import tempfile
import zipfile
import shutil
import asyncio

from version import APP_VERSION, DATA_SCHEMA_VERSION

router = APIRouter(prefix="/updates", tags=["updates"])

# Current version - update this when releasing new versions
CURRENT_VERSION = APP_VERSION
DATA_VERSION = DATA_SCHEMA_VERSION

# Track startup state
_startup_state = {
    "status": "starting",  # starting, upgrading, reindexing, ready
    "message": "Starting LocalBook...",
    "progress": 0,
    "previous_version": None,
    "is_upgrade": False
}

def get_stored_version() -> Optional[str]:
    """Get the previously stored version from data directory"""
    from config import settings
    version_file = settings.data_dir / ".version"
    if version_file.exists():
        return version_file.read_text().strip()
    return None

def store_current_version():
    """Store the current version to data directory"""
    from config import settings
    version_file = settings.data_dir / ".version"
    version_file.parent.mkdir(parents=True, exist_ok=True)
    version_file.write_text(CURRENT_VERSION)

def check_if_upgrade() -> tuple[bool, Optional[str]]:
    """Check if this is an upgrade from a previous version"""
    previous = get_stored_version()
    if previous is None:
        # First run or no version file
        return False, None
    if previous != CURRENT_VERSION:
        return True, previous
    return False, previous

def set_startup_status(status: str, message: str, progress: int = 0):
    """Update the startup status"""
    _startup_state["status"] = status
    _startup_state["message"] = message
    _startup_state["progress"] = progress

def mark_startup_complete():
    """Mark startup as complete and store version"""
    _startup_state["status"] = "ready"
    _startup_state["message"] = "LocalBook is ready"
    _startup_state["progress"] = 100
    store_current_version()

# GitHub repo info
GITHUB_OWNER = "patsteph"  # Update with your GitHub username
GITHUB_REPO = "LocalBook"


class VersionInfo(BaseModel):
    current_version: str
    data_version: str
    latest_version: Optional[str] = None
    update_available: bool = False
    release_notes: Optional[str] = None
    download_url: Optional[str] = None
    asset_download_url: Optional[str] = None  # Direct download URL for .zip
    error: Optional[str] = None


class UpdateResult(BaseModel):
    success: bool
    message: str
    new_version: Optional[str] = None
    progress: Optional[int] = None  # Download progress percentage


# Track download progress
_download_state = {
    "downloading": False,
    "progress": 0,
    "message": "",
    "error": None
}


@router.get("/check", response_model=VersionInfo)
async def check_for_updates():
    """Check GitHub for the latest release"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Get latest release from GitHub API
            response = await client.get(
                f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest",
                headers={"Accept": "application/vnd.github.v3+json"}
            )
            
            if response.status_code == 200:
                release = response.json()
                latest_version = release.get("tag_name", "").lstrip("v")
                release_notes = release.get("body", "")
                download_url = release.get("html_url", "")
                
                # Find the .zip asset for direct download
                asset_download_url = None
                assets = release.get("assets", [])
                for asset in assets:
                    if asset.get("name", "").endswith(".zip"):
                        asset_download_url = asset.get("browser_download_url")
                        break
                
                # Compare versions
                update_available = _compare_versions(CURRENT_VERSION, latest_version)
                
                return VersionInfo(
                    current_version=CURRENT_VERSION,
                    data_version=DATA_VERSION,
                    latest_version=latest_version,
                    update_available=update_available,
                    release_notes=release_notes[:500] if release_notes else None,
                    download_url=download_url,
                    asset_download_url=asset_download_url
                )
            elif response.status_code == 404:
                # No releases yet
                return VersionInfo(
                    current_version=CURRENT_VERSION,
                    data_version=DATA_VERSION,
                    latest_version=None,
                    update_available=False,
                    error="No releases found on GitHub"
                )
            else:
                return VersionInfo(
                    current_version=CURRENT_VERSION,
                    data_version=DATA_VERSION,
                    error=f"GitHub API error: {response.status_code}"
                )
    except Exception as e:
        return VersionInfo(
            current_version=CURRENT_VERSION,
            data_version=DATA_VERSION,
            error=f"Failed to check for updates: {str(e)}"
        )


@router.get("/version")
async def get_current_version():
    """Get the current version"""
    return {"version": CURRENT_VERSION}


class StartupStatus(BaseModel):
    status: str  # starting, upgrading, migrating, reindexing, ready
    message: str
    progress: int
    current_version: str
    data_version: str
    previous_version: Optional[str] = None
    is_upgrade: bool = False


@router.get("/startup-status", response_model=StartupStatus)
async def get_startup_status():
    """Get the current startup status for splash screen"""
    return StartupStatus(
        status=_startup_state["status"],
        message=_startup_state["message"],
        progress=_startup_state["progress"],
        current_version=CURRENT_VERSION,
        data_version=DATA_VERSION,
        previous_version=_startup_state["previous_version"],
        is_upgrade=_startup_state["is_upgrade"]
    )


@router.post("/pull", response_model=UpdateResult)
async def pull_updates():
    """Pull latest changes from GitHub (requires git)"""
    try:
        # Get the project root directory
        project_root = Path(__file__).parent.parent.parent
        
        # Check if this is a git repository
        git_dir = project_root / ".git"
        if not git_dir.exists():
            return UpdateResult(
                success=False,
                message="This is a production install. To update: 1) Download the latest release from GitHub, 2) Replace your LocalBook.app with the new version, 3) Restart the app. Your data in ~/Library/Application Support/LocalBook/ will be preserved."
            )
        
        # Run git pull
        result = subprocess.run(
            ["git", "pull", "origin", "main"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode == 0:
            output = result.stdout.strip()
            if "Already up to date" in output:
                return UpdateResult(
                    success=True,
                    message="Already up to date!"
                )
            else:
                # Get the new version after pull
                return UpdateResult(
                    success=True,
                    message="Update pulled successfully! Please restart the application to apply changes.",
                    new_version=await _get_version_after_pull()
                )
        else:
            return UpdateResult(
                success=False,
                message=f"Git pull failed: {result.stderr}"
            )
    except subprocess.TimeoutExpired:
        return UpdateResult(
            success=False,
            message="Update timed out. Please try again."
        )
    except FileNotFoundError:
        return UpdateResult(
            success=False,
            message="Git not found. Please install git or update manually."
        )
    except Exception as e:
        return UpdateResult(
            success=False,
            message=f"Update failed: {str(e)}"
        )


def _compare_versions(current: str, latest: str) -> bool:
    """Compare version strings. Returns True if latest > current."""
    try:
        current_parts = [int(x) for x in current.split(".")]
        latest_parts = [int(x) for x in latest.split(".")]
        
        # Pad with zeros if needed
        while len(current_parts) < 3:
            current_parts.append(0)
        while len(latest_parts) < 3:
            latest_parts.append(0)
        
        return latest_parts > current_parts
    except (ValueError, AttributeError):
        return False


async def _get_version_after_pull() -> Optional[str]:
    """Read version from file after pull"""
    try:
        # Try to read version from this file after pull
        # In a real scenario, you'd read from a VERSION file or package.json
        return CURRENT_VERSION
    except Exception:
        return None


def _get_current_app_path() -> Optional[Path]:
    """Get the path to the currently running .app bundle"""
    if not getattr(sys, 'frozen', False):
        return None  # Not a bundled app
    
    # sys.executable points to the binary inside the app
    # e.g., /Applications/LocalBook.app/Contents/Resources/resources/backend/localbook-backend/localbook-backend
    exe_path = Path(sys.executable)
    
    # Walk up to find the .app bundle
    for parent in exe_path.parents:
        if parent.suffix == '.app':
            return parent
    
    return None


class DownloadProgress(BaseModel):
    downloading: bool
    progress: int
    message: str
    error: Optional[str] = None


@router.get("/download-progress", response_model=DownloadProgress)
async def get_download_progress():
    """Get the current download progress"""
    return DownloadProgress(
        downloading=_download_state["downloading"],
        progress=_download_state["progress"],
        message=_download_state["message"],
        error=_download_state["error"]
    )


@router.post("/download-and-install", response_model=UpdateResult)
async def download_and_install_update():
    """Download the latest release and prepare for installation"""
    global _download_state
    
    # Check if already downloading
    if _download_state["downloading"]:
        return UpdateResult(
            success=False,
            message="Download already in progress"
        )
    
    try:
        _download_state = {"downloading": True, "progress": 0, "message": "Checking for updates...", "error": None}
        
        # Get the latest release info
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest",
                headers={"Accept": "application/vnd.github.v3+json"}
            )
            
            if response.status_code != 200:
                _download_state = {"downloading": False, "progress": 0, "message": "", "error": "Failed to fetch release info"}
                return UpdateResult(success=False, message="Failed to fetch release info")
            
            release = response.json()
            latest_version = release.get("tag_name", "").lstrip("v")
            
            # Find the .zip asset
            asset_url = None
            asset_name = None
            for asset in release.get("assets", []):
                if asset.get("name", "").endswith(".zip"):
                    asset_url = asset.get("browser_download_url")
                    asset_name = asset.get("name")
                    break
            
            if not asset_url:
                _download_state = {"downloading": False, "progress": 0, "message": "", "error": "No download available"}
                return UpdateResult(
                    success=False,
                    message="No downloadable update found. The release may not have a .zip file attached yet."
                )
            
            _download_state["message"] = f"Downloading {asset_name}..."
            _download_state["progress"] = 10
            
            # Download the zip file
            download_path = Path(tempfile.gettempdir()) / asset_name
            
            async with client.stream("GET", asset_url, follow_redirects=True) as resp:
                total_size = int(resp.headers.get("content-length", 0))
                downloaded = 0
                
                with open(download_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=8192):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            progress = int(10 + (downloaded / total_size) * 60)  # 10-70%
                            _download_state["progress"] = progress
            
            _download_state["message"] = "Extracting update..."
            _download_state["progress"] = 75
            
            # Extract the zip
            extract_dir = Path(tempfile.gettempdir()) / "LocalBook_update"
            if extract_dir.exists():
                shutil.rmtree(extract_dir)
            extract_dir.mkdir(parents=True)
            
            with zipfile.ZipFile(download_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
            
            # Find the .app in the extracted contents
            new_app_path = None
            for item in extract_dir.iterdir():
                if item.suffix == '.app':
                    new_app_path = item
                    break
            
            if not new_app_path:
                # Maybe it's nested in a folder
                for subdir in extract_dir.iterdir():
                    if subdir.is_dir():
                        for item in subdir.iterdir():
                            if item.suffix == '.app':
                                new_app_path = item
                                break
            
            if not new_app_path:
                _download_state = {"downloading": False, "progress": 0, "message": "", "error": "Invalid update package"}
                return UpdateResult(success=False, message="Could not find LocalBook.app in the downloaded update")
            
            _download_state["message"] = "Preparing installation..."
            _download_state["progress"] = 85
            
            # Get current app location
            current_app = _get_current_app_path()
            if not current_app:
                # Fallback to /Applications
                current_app = Path("/Applications/LocalBook.app")
            
            # Create the install script that will run after we quit
            install_script = Path(tempfile.gettempdir()) / "localbook_install.sh"
            install_script.write_text(f'''#!/bin/bash
# Wait for the app to quit
sleep 2

# Remove old app
rm -rf "{current_app}"

# Move new app into place
mv "{new_app_path}" "{current_app}"

# Remove quarantine flag (macOS Gatekeeper)
xattr -cr "{current_app}"

# Clean up
rm -rf "{extract_dir}"
rm -f "{download_path}"

# Relaunch
open "{current_app}"

# Self-destruct
rm -f "$0"
''')
            install_script.chmod(0o755)
            
            _download_state["message"] = "Ready to install! Click 'Install & Restart' to complete."
            _download_state["progress"] = 100
            
            return UpdateResult(
                success=True,
                message="Update downloaded! Ready to install.",
                new_version=latest_version
            )
            
    except Exception as e:
        _download_state = {"downloading": False, "progress": 0, "message": "", "error": str(e)}
        return UpdateResult(success=False, message=f"Download failed: {str(e)}")


@router.post("/install-and-restart", response_model=UpdateResult)
async def install_and_restart():
    """Execute the install script and quit the app"""
    install_script = Path(tempfile.gettempdir()) / "localbook_install.sh"
    
    if not install_script.exists():
        return UpdateResult(success=False, message="No update ready to install. Please download first.")
    
    try:
        # Run the install script in background
        subprocess.Popen(
            ["/bin/bash", str(install_script)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        
        # Reset download state
        global _download_state
        _download_state = {"downloading": False, "progress": 0, "message": "", "error": None}
        
        # The frontend will handle quitting the app
        return UpdateResult(
            success=True,
            message="Installing update... The app will restart shortly."
        )
        
    except Exception as e:
        return UpdateResult(success=False, message=f"Failed to start installation: {str(e)}")
