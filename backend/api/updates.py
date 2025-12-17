"""Update checking API endpoints"""
import httpx
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from pathlib import Path
import json
import subprocess
import os

router = APIRouter(prefix="/updates", tags=["updates"])

# Current version - update this when releasing new versions
CURRENT_VERSION = "0.2.0"

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
    version_file = Path("data/.version")
    if version_file.exists():
        return version_file.read_text().strip()
    return None

def store_current_version():
    """Store the current version to data directory"""
    version_file = Path("data/.version")
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
    latest_version: Optional[str] = None
    update_available: bool = False
    release_notes: Optional[str] = None
    download_url: Optional[str] = None
    error: Optional[str] = None


class UpdateResult(BaseModel):
    success: bool
    message: str
    new_version: Optional[str] = None


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
                
                # Compare versions
                update_available = _compare_versions(CURRENT_VERSION, latest_version)
                
                return VersionInfo(
                    current_version=CURRENT_VERSION,
                    latest_version=latest_version,
                    update_available=update_available,
                    release_notes=release_notes[:500] if release_notes else None,
                    download_url=download_url
                )
            elif response.status_code == 404:
                # No releases yet
                return VersionInfo(
                    current_version=CURRENT_VERSION,
                    latest_version=None,
                    update_available=False,
                    error="No releases found on GitHub"
                )
            else:
                return VersionInfo(
                    current_version=CURRENT_VERSION,
                    error=f"GitHub API error: {response.status_code}"
                )
    except Exception as e:
        return VersionInfo(
            current_version=CURRENT_VERSION,
            error=f"Failed to check for updates: {str(e)}"
        )


@router.get("/version")
async def get_current_version():
    """Get the current version"""
    return {"version": CURRENT_VERSION}


class StartupStatus(BaseModel):
    status: str  # starting, upgrading, reindexing, ready
    message: str
    progress: int
    current_version: str
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
                message="Not a git repository. Please update manually."
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
