"""People Profiler data models for coaching and team management notebooks.

Supports aggregating social media profiles, internal notes, and LLM-generated
coaching insights into unified person profiles.
"""
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
import uuid


class SocialPlatform(str, Enum):
    """Supported social media platforms"""
    LINKEDIN = "linkedin"
    TWITTER = "twitter"
    GITHUB = "github"
    INSTAGRAM = "instagram"
    PERSONAL_SITE = "personal_site"


PLATFORM_DOMAINS = {
    SocialPlatform.LINKEDIN: ["linkedin.com"],
    SocialPlatform.TWITTER: ["x.com", "twitter.com"],
    SocialPlatform.GITHUB: ["github.com"],
    SocialPlatform.INSTAGRAM: ["instagram.com"],
}

PLATFORM_LOGIN_URLS = {
    SocialPlatform.LINKEDIN: "https://www.linkedin.com/login",
    SocialPlatform.TWITTER: "https://x.com/i/flow/login",
    SocialPlatform.INSTAGRAM: "https://www.instagram.com/accounts/login/",
}

PLATFORM_AUTH_SUCCESS_PATTERNS = {
    SocialPlatform.LINKEDIN: "**/feed**",
    SocialPlatform.TWITTER: "**/home**",
    SocialPlatform.INSTAGRAM: "**instagram.com/**",
}


class SocialAuthConfig(BaseModel):
    """Tracks an authenticated platform session (encrypted at rest)."""
    platform: str
    authenticated: bool = False
    auth_state_path: str = ""       # Path to .enc file (Fernet encrypted)
    last_verified: Optional[str] = None
    max_age_days: int = 30
    created_at: Optional[str] = None

    def is_expired(self) -> bool:
        if not self.last_verified:
            return True
        try:
            verified = datetime.fromisoformat(self.last_verified)
            return (datetime.utcnow() - verified).days > self.max_age_days
        except (ValueError, TypeError):
            return True


class WorkExperience(BaseModel):
    """A single work experience entry"""
    title: str = ""
    company: str = ""
    dates: str = ""             # "Jan 2020 - Present"
    description: str = ""
    is_current: bool = False


class Education(BaseModel):
    """A single education entry"""
    school: str = ""
    degree: str = ""
    field: str = ""
    dates: str = ""


class SocialPost(BaseModel):
    """A single social media post"""
    platform: str = ""
    text: str = ""
    date: str = ""
    url: str = ""
    likes: int = 0
    comments: int = 0
    shares: int = 0


class RecentActivity(BaseModel):
    """A single recent activity item from a platform"""
    platform: str = ""
    title: str = ""                 # Post title, commit message, article headline
    summary: str = ""               # Brief description of the content
    url: str = ""
    date: str = ""                  # ISO date
    content_type: str = ""          # article, post, comment, commit, photo, share, etc.
    engagement: Dict[str, int] = Field(default_factory=dict)  # likes, comments, etc.


class ActivityProfile(BaseModel):
    """Aggregated activity analysis across all platforms for a person.

    Built from the last N items per platform. Provides a quick overview
    of what someone has been working on and how active they are.
    """
    # Per-platform frequency classification
    platform_frequency: Dict[str, str] = Field(default_factory=dict)
    # e.g. {"linkedin": "weekly", "github": "daily", "twitter": "inactive"}
    # Values: daily, several_per_week, weekly, biweekly, monthly, rarely, inactive

    # Content type breakdown per platform
    content_types: Dict[str, List[str]] = Field(default_factory=dict)
    # e.g. {"linkedin": ["article", "share", "comment"], "github": ["commit", "pr"]}

    # Recent items (last 3 per platform, newest first)
    recent_items: List[RecentActivity] = Field(default_factory=list)

    # LLM-generated summary of what they've been focused on
    focus_summary: str = ""         # "Focused on AI/ML content, sharing industry articles"
    topics: List[str] = Field(default_factory=list)  # ["AI", "leadership", "product"]

    # Per-platform last active date (ISO string, e.g. "2026-02-08T...")
    platform_last_active: Dict[str, str] = Field(default_factory=dict)
    # e.g. {"linkedin": "2026-01-15", "github": "2026-02-08"}

    # Overall activity level
    overall_frequency: str = ""     # daily, weekly, monthly, rarely, inactive
    overall_last_active: str = ""   # most recent date across all platforms
    last_analyzed: Optional[str] = None


class CoachingNote(BaseModel):
    """A private coaching/manager note about this person"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    text: str = ""
    category: str = "general"   # general, strength, growth_area, goal, observation
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: Optional[str] = None


class CoachingGoal(BaseModel):
    """A coaching goal for this person"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    goal: str = ""
    target_date: Optional[str] = None
    status: str = "active"      # active, completed, paused, dropped
    notes: str = ""
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class ProfileSource(BaseModel):
    """Tracks where profile data came from"""
    platform: str               # SocialPlatform value or "manual", "web_search"
    url: str = ""
    captured_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    data_fields: List[str] = Field(default_factory=list)
    success: bool = True
    error: Optional[str] = None


class PersonProfile(BaseModel):
    """Core person profile aggregated from multiple sources.
    
    This is the central data structure for a team member in a coaching notebook.
    It combines data from social platforms, web search, and manager notes.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    notebook_id: str = ""
    name: str = ""

    # Identity
    headline: str = ""              # "VP of Engineering at Acme"
    bio: str = ""                   # Best bio from any source
    photo_url: str = ""
    location: str = ""
    email: str = ""                 # Manual entry only

    # Social Links (user-provided URLs)
    social_links: Dict[str, str] = Field(default_factory=dict)

    # Professional (primarily from LinkedIn)
    current_role: str = ""
    current_company: str = ""
    experience: List[WorkExperience] = Field(default_factory=list)
    education: List[Education] = Field(default_factory=list)
    skills: List[str] = Field(default_factory=list)

    # Activity (from periodic collection)
    linkedin_posts: List[SocialPost] = Field(default_factory=list)
    tweets: List[SocialPost] = Field(default_factory=list)
    github_activity: Dict[str, Any] = Field(default_factory=dict)
    instagram_posts: List[SocialPost] = Field(default_factory=list)
    blog_posts: List[SocialPost] = Field(default_factory=list)

    # Internal (manager/coach only — never sent to external services)
    coaching_notes: List[CoachingNote] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    goals: List[CoachingGoal] = Field(default_factory=list)

    # Activity profiling (frequency, content types, recent items)
    activity_profile: ActivityProfile = Field(default_factory=ActivityProfile)

    # LLM-generated insights
    coaching_insights: Dict[str, Any] = Field(default_factory=dict)

    # Change detection
    recent_changes: List[Dict[str, Any]] = Field(default_factory=list)

    # Collection metadata
    sources: List[ProfileSource] = Field(default_factory=list)
    collection_schedule: str = "weekly"     # daily, weekly, manual
    last_collected: Dict[str, str] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class PeopleNotebookConfig(BaseModel):
    """Configuration for a people/coaching notebook.
    
    Stored at ~/Library/Application Support/LocalBook/notebooks/{id}/people_config.yaml
    
    coaching_enabled: When True, the notebook is used for active coaching/personnel
    management. Shows coaching tab, generates coaching insights, goals, etc.
    When False, the notebook is for research/profiling only — strengths and
    growth areas are still surfaced but coaching-specific features are hidden.
    """
    notebook_id: str = ""
    notebook_name: str = ""
    team_name: str = ""             # Optional team label
    coaching_enabled: bool = False  # Only True for coaching/personnel notebooks
    members: List[PersonProfile] = Field(default_factory=list)
    social_auth: Dict[str, SocialAuthConfig] = Field(default_factory=dict)
    collection_schedule: str = "weekly"
    last_collection_run: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
