"""People Profiler API endpoints for coaching/team management notebooks.

Handles:
- Social platform authentication (Playwright + Fernet encryption)
- Team member CRUD (add/edit/remove members with social URLs)
- Profile retrieval (aggregated view)
- Coaching notes management
- Collection triggers
"""

import logging
from typing import List, Optional, Dict, Any
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.social_auth import social_auth
from models.person_profile import (
    PersonProfile, PeopleNotebookConfig, CoachingNote, CoachingGoal,
    SocialPlatform,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/people", tags=["people"])


# =============================================================================
# Request/Response Models
# =============================================================================

class AddMemberRequest(BaseModel):
    name: str
    social_links: Dict[str, str] = Field(default_factory=dict)
    email: Optional[str] = None
    current_role: Optional[str] = None
    current_company: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    initial_notes: Optional[str] = None


class UpdateMemberRequest(BaseModel):
    name: Optional[str] = None
    social_links: Optional[Dict[str, str]] = None
    email: Optional[str] = None
    current_role: Optional[str] = None
    current_company: Optional[str] = None
    tags: Optional[List[str]] = None
    collection_schedule: Optional[str] = None


class AddNoteRequest(BaseModel):
    text: str
    category: str = "general"


class AddGoalRequest(BaseModel):
    goal: str
    target_date: Optional[str] = None
    notes: str = ""


class UpdateGoalRequest(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None
    target_date: Optional[str] = None


class PeopleConfigRequest(BaseModel):
    """Initial config when creating a people notebook."""
    notebook_name: str = ""
    team_name: str = ""
    coaching_enabled: bool = False
    members: List[AddMemberRequest] = Field(default_factory=list)
    collection_schedule: str = "weekly"


# =============================================================================
# Config helpers — load/save people config YAML
# =============================================================================

def _get_config_path(notebook_id: str):
    from config import settings
    return settings.data_dir / "notebooks" / notebook_id / "people_config.yaml"


def _load_config(notebook_id: str) -> PeopleNotebookConfig:
    import yaml
    config_path = _get_config_path(notebook_id)
    if config_path.exists():
        with open(config_path, "r") as f:
            data = yaml.safe_load(f) or {}
        config = PeopleNotebookConfig(**data)
        # Auto-detect coaching mode from existing data — supersedes manual toggle
        if not config.coaching_enabled:
            config.coaching_enabled = _should_auto_enable_coaching(config, notebook_id)
        return config
    return PeopleNotebookConfig(notebook_id=notebook_id)


# Coaching keywords used during auto-detection (mirrors CollectorSetupWizard)
_COACHING_KEYWORDS = {
    'coach', 'coaching', '1:1', '1-on-1', 'one on one', 'one-on-one',
    'team member', 'direct report', 'personal development', 'mentoring',
    'mentor', 'performance review', 'people management', 'team management',
    'growth plan', 'development plan', 'leadership development',
    'personnel', 'career', 'career coaching',
}


def _should_auto_enable_coaching(config: PeopleNotebookConfig, notebook_id: str) -> bool:
    """Auto-detect if a people notebook is coaching-oriented.
    
    Returns True if any member has coaching notes or goals,
    or if the collector config intent matches coaching keywords.
    This supersedes the manual toggle for existing notebooks.
    """
    # Check if any member has coaching notes or goals
    for member in config.members:
        if member.coaching_notes or member.goals:
            return True
    
    # Check collector config intent for coaching keywords
    try:
        from config import settings
        import yaml as _yaml
        collector_path = settings.data_dir / "notebooks" / notebook_id / "collector_config.yaml"
        if collector_path.exists():
            with open(collector_path, "r") as f:
                cdata = _yaml.safe_load(f) or {}
            intent = (cdata.get("intent", "") + " " + cdata.get("subject", "")).lower()
            if any(kw in intent for kw in _COACHING_KEYWORDS):
                return True
    except Exception:
        pass
    
    return False


def _save_config(notebook_id: str, config: PeopleNotebookConfig):
    import yaml
    config_path = _get_config_path(notebook_id)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config.updated_at = datetime.utcnow().isoformat()
    with open(config_path, "w") as f:
        yaml.dump(config.model_dump(), f, default_flow_style=False, sort_keys=False)


# =============================================================================
# Social Auth Endpoints
# =============================================================================

@router.get("/auth/status")
async def get_auth_status():
    """Get authentication status for all supported social platforms."""
    return social_auth.get_auth_status()


@router.post("/auth/{platform}")
async def authenticate_platform(platform: str):
    """Trigger authentication flow for a social platform.
    
    Opens a real browser window for the user to log in.
    Session is encrypted with Fernet AES before saving to disk.
    """
    valid_platforms = [p.value for p in SocialPlatform if p not in (SocialPlatform.PERSONAL_SITE, SocialPlatform.GITHUB)]
    if platform not in valid_platforms:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid platform: {platform}. Valid: {valid_platforms}"
        )

    try:
        config = await social_auth.authenticate_platform(platform)
        return {
            "success": True,
            "platform": platform,
            "authenticated": config.authenticated,
            "message": f"Successfully connected to {platform}",
        }
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Auth failed for {platform}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Authentication failed: {str(e)}"
        )


@router.delete("/auth/{platform}")
async def disconnect_platform(platform: str):
    """Disconnect a social platform (delete encrypted session)."""
    removed = social_auth.disconnect_platform(platform)
    return {
        "success": removed,
        "platform": platform,
        "message": f"{'Disconnected' if removed else 'No session found for'} {platform}",
    }


# =============================================================================
# Notebook Config Endpoints
# =============================================================================

@router.post("/{notebook_id}/config")
async def create_people_config(notebook_id: str, request: PeopleConfigRequest):
    """Create or update people notebook configuration."""
    config = _load_config(notebook_id)
    config.notebook_id = notebook_id
    config.notebook_name = request.notebook_name or config.notebook_name
    config.team_name = request.team_name or config.team_name
    config.coaching_enabled = request.coaching_enabled
    config.collection_schedule = request.collection_schedule

    # Add initial members
    for member_req in request.members:
        person = PersonProfile(
            notebook_id=notebook_id,
            name=member_req.name,
            social_links=member_req.social_links,
            email=member_req.email or "",
            current_role=member_req.current_role or "",
            current_company=member_req.current_company or "",
            tags=member_req.tags,
            collection_schedule=request.collection_schedule,
        )
        if member_req.initial_notes:
            person.coaching_notes.append(
                CoachingNote(text=member_req.initial_notes, category="general")
            )
        config.members.append(person)

    # Capture current auth status
    config.social_auth = {
        k: _auth_status_to_config(v)
        for k, v in social_auth.get_auth_status().items()
    }

    _save_config(notebook_id, config)

    return {
        "success": True,
        "notebook_id": notebook_id,
        "members_added": len(request.members),
        "total_members": len(config.members),
    }


@router.get("/{notebook_id}/config")
async def get_people_config(notebook_id: str):
    """Get people notebook configuration."""
    config_path = _get_config_path(notebook_id)
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="People config not found")
    config = _load_config(notebook_id)
    return config.model_dump()


@router.patch("/{notebook_id}/config/coaching")
async def toggle_coaching(notebook_id: str, enabled: bool = True):
    """Toggle coaching mode for a people notebook.
    
    When enabled, coaching tab, coaching insights, goals, and coaching notes
    become visible. When disabled, the notebook is research/profiling only.
    """
    config = _load_config(notebook_id)
    if not config.notebook_id:
        raise HTTPException(status_code=404, detail="People config not found")
    config.coaching_enabled = enabled
    _save_config(notebook_id, config)
    return {"success": True, "coaching_enabled": enabled}


# =============================================================================
# Member Management Endpoints
# =============================================================================

@router.get("/{notebook_id}/members")
async def list_members(notebook_id: str):
    """List all team members in a people notebook."""
    config = _load_config(notebook_id)
    return {
        "notebook_id": notebook_id,
        "members": [m.model_dump() for m in config.members],
        "total": len(config.members),
    }


@router.post("/{notebook_id}/members")
async def add_member(notebook_id: str, request: AddMemberRequest):
    """Add a new team member to the notebook."""
    config = _load_config(notebook_id)

    person = PersonProfile(
        notebook_id=notebook_id,
        name=request.name,
        social_links=request.social_links,
        email=request.email or "",
        current_role=request.current_role or "",
        current_company=request.current_company or "",
        tags=request.tags,
        collection_schedule=config.collection_schedule,
    )
    if request.initial_notes:
        person.coaching_notes.append(
            CoachingNote(text=request.initial_notes, category="general")
        )

    config.members.append(person)
    _save_config(notebook_id, config)

    return {
        "success": True,
        "member_id": person.id,
        "name": person.name,
        "total_members": len(config.members),
    }


@router.get("/{notebook_id}/members/{member_id}")
async def get_member(notebook_id: str, member_id: str):
    """Get a single team member's full profile."""
    config = _load_config(notebook_id)
    member = _find_member(config, member_id)
    return member.model_dump()


@router.put("/{notebook_id}/members/{member_id}")
async def update_member(notebook_id: str, member_id: str, request: UpdateMemberRequest):
    """Update a team member's info."""
    config = _load_config(notebook_id)
    member = _find_member(config, member_id)

    if request.name is not None:
        member.name = request.name
    if request.social_links is not None:
        member.social_links.update(request.social_links)
    if request.email is not None:
        member.email = request.email
    if request.current_role is not None:
        member.current_role = request.current_role
    if request.current_company is not None:
        member.current_company = request.current_company
    if request.tags is not None:
        member.tags = request.tags
    if request.collection_schedule is not None:
        member.collection_schedule = request.collection_schedule

    member.updated_at = datetime.utcnow().isoformat()
    _save_config(notebook_id, config)
    return {"success": True, "member_id": member_id}


@router.get("/{notebook_id}/members/{member_id}/activity")
async def get_member_activity(notebook_id: str, member_id: str):
    """Get a member's activity profile (frequency, content types, recent items)."""
    config = _load_config(notebook_id)
    member = _find_member(config, member_id)
    return member.activity_profile.model_dump()


@router.post("/{notebook_id}/members/{member_id}/analyze-activity")
async def analyze_member_activity(notebook_id: str, member_id: str):
    """Re-analyze a member's activity profile from existing collected data."""
    from services.activity_analyzer import analyze_activity, generate_activity_insights
    config = _load_config(notebook_id)
    member = _find_member(config, member_id)
    member.activity_profile = analyze_activity(member)
    # Generate LLM insights
    try:
        focus = await generate_activity_insights(
            member.name, member.activity_profile, member.github_activity
        )
        if focus:
            member.activity_profile.focus_summary = focus
    except Exception:
        pass
    member.updated_at = datetime.utcnow().isoformat()
    _save_config(notebook_id, config)
    return member.activity_profile.model_dump()


@router.delete("/{notebook_id}/members/{member_id}")
async def remove_member(notebook_id: str, member_id: str):
    """Remove a team member from the notebook."""
    config = _load_config(notebook_id)
    original_count = len(config.members)
    config.members = [m for m in config.members if m.id != member_id]

    if len(config.members) == original_count:
        raise HTTPException(status_code=404, detail="Member not found")

    _save_config(notebook_id, config)
    return {"success": True, "member_id": member_id, "remaining": len(config.members)}


# =============================================================================
# Coaching Notes & Goals
# =============================================================================

@router.post("/{notebook_id}/members/{member_id}/notes")
async def add_coaching_note(notebook_id: str, member_id: str, request: AddNoteRequest):
    """Add a coaching note to a team member."""
    config = _load_config(notebook_id)
    member = _find_member(config, member_id)

    note = CoachingNote(text=request.text, category=request.category)
    member.coaching_notes.append(note)
    member.updated_at = datetime.utcnow().isoformat()
    _save_config(notebook_id, config)

    # Re-index coaching notes into RAG (background)
    import asyncio
    from services.profile_indexer import profile_indexer
    asyncio.create_task(profile_indexer.index_coaching_notes(notebook_id, member))

    return {"success": True, "note_id": note.id, "total_notes": len(member.coaching_notes)}


@router.get("/{notebook_id}/members/{member_id}/notes")
async def get_coaching_notes(notebook_id: str, member_id: str):
    """Get all coaching notes for a team member."""
    config = _load_config(notebook_id)
    member = _find_member(config, member_id)
    return {
        "member_id": member_id,
        "name": member.name,
        "notes": [n.model_dump() for n in member.coaching_notes],
    }


@router.delete("/{notebook_id}/members/{member_id}/notes/{note_id}")
async def delete_coaching_note(notebook_id: str, member_id: str, note_id: str):
    """Delete a coaching note from a team member."""
    config = _load_config(notebook_id)
    member = _find_member(config, member_id)

    original_len = len(member.coaching_notes)
    member.coaching_notes = [n for n in member.coaching_notes if n.id != note_id]
    if len(member.coaching_notes) == original_len:
        raise HTTPException(status_code=404, detail="Note not found")

    member.updated_at = datetime.utcnow().isoformat()
    _save_config(notebook_id, config)
    return {"success": True, "note_id": note_id, "remaining_notes": len(member.coaching_notes)}


@router.post("/{notebook_id}/members/{member_id}/goals")
async def add_coaching_goal(notebook_id: str, member_id: str, request: AddGoalRequest):
    """Add a coaching goal to a team member."""
    config = _load_config(notebook_id)
    member = _find_member(config, member_id)

    goal = CoachingGoal(
        goal=request.goal,
        target_date=request.target_date,
        notes=request.notes,
    )
    member.goals.append(goal)
    member.updated_at = datetime.utcnow().isoformat()
    _save_config(notebook_id, config)

    return {"success": True, "goal_id": goal.id}


@router.put("/{notebook_id}/members/{member_id}/goals/{goal_id}")
async def update_coaching_goal(
    notebook_id: str, member_id: str, goal_id: str, request: UpdateGoalRequest
):
    """Update a coaching goal's status or notes."""
    config = _load_config(notebook_id)
    member = _find_member(config, member_id)

    goal = next((g for g in member.goals if g.id == goal_id), None)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    if request.status is not None:
        goal.status = request.status
    if request.notes is not None:
        goal.notes = request.notes
    if request.target_date is not None:
        goal.target_date = request.target_date

    _save_config(notebook_id, config)
    return {"success": True, "goal_id": goal_id, "status": goal.status}


@router.delete("/{notebook_id}/members/{member_id}/goals/{goal_id}")
async def delete_coaching_goal(notebook_id: str, member_id: str, goal_id: str):
    """Delete a coaching goal from a team member."""
    config = _load_config(notebook_id)
    member = _find_member(config, member_id)

    original_len = len(member.goals)
    member.goals = [g for g in member.goals if g.id != goal_id]
    if len(member.goals) == original_len:
        raise HTTPException(status_code=404, detail="Goal not found")

    member.updated_at = datetime.utcnow().isoformat()
    _save_config(notebook_id, config)
    return {"success": True, "goal_id": goal_id, "remaining_goals": len(member.goals)}


# =============================================================================
# Collection Trigger
# =============================================================================

@router.post("/{notebook_id}/members/{member_id}/collect")
async def collect_member_profile(notebook_id: str, member_id: str):
    """Trigger social profile collection for a specific team member."""
    config = _load_config(notebook_id)
    member = _find_member(config, member_id)

    if not member.social_links:
        raise HTTPException(
            status_code=400,
            detail="No social links configured for this member"
        )

    # Import collector lazily to avoid circular imports
    try:
        from services.social_collector import social_collector
        result = await social_collector.collect_person(member, notebook_id)
        return {
            "success": True,
            "member_id": member_id,
            "name": member.name,
            "platforms_collected": result.get("platforms_collected", []),
            "errors": result.get("errors", []),
        }
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="Social collector service not available. Ensure Playwright is installed."
        )
    except Exception as e:
        logger.error(f"Collection failed for {member.name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{notebook_id}/refresh-insights")
async def refresh_notebook_insights(notebook_id: str):
    """Manually trigger coaching insights refresh for all members in a people notebook."""
    from services.coaching_insights import refresh_notebook_insights
    try:
        result = await refresh_notebook_insights(notebook_id)
        return {"success": True, "notebook_id": notebook_id, "result": result}
    except Exception as e:
        logger.error(f"Coaching refresh failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{notebook_id}/collect-all")
async def collect_all_members(notebook_id: str):
    """Trigger collection for all team members in the notebook."""
    config = _load_config(notebook_id)

    if not config.members:
        raise HTTPException(status_code=400, detail="No members in this notebook")

    try:
        from services.social_collector import social_collector
        results = await social_collector.collect_all(config.members, notebook_id)
        config.last_collection_run = datetime.utcnow().isoformat()
        _save_config(notebook_id, config)
        return {
            "success": True,
            "notebook_id": notebook_id,
            "members_collected": len(results),
            "results": results,
        }
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="Social collector service not available."
        )
    except Exception as e:
        logger.error(f"Bulk collection failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Helpers
# =============================================================================

def _find_member(config: PeopleNotebookConfig, member_id: str) -> PersonProfile:
    member = next((m for m in config.members if m.id == member_id), None)
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    return member


def _auth_status_to_config(status: Dict[str, Any]) -> dict:
    return {
        "platform": status.get("platform", ""),
        "authenticated": status.get("authenticated", False),
        "auth_state_path": "",
        "last_verified": datetime.utcnow().isoformat() if status.get("authenticated") else None,
    }
