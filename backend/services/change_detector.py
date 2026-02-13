"""Change Detector — compares previous vs current PersonProfile snapshots.

After each collection run, this service diffs the old and new profile data
to detect meaningful changes:
- Role/company changes ("Jane moved from Google to Stripe")
- New skills or interests
- Activity pattern shifts (new topics, frequency changes)
- Headline changes

Changes are returned as a list of ChangeEvent dicts and can be surfaced
in the Coaching tab as alerts.
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from models.person_profile import PersonProfile

logger = logging.getLogger(__name__)


class ChangeEvent:
    """A detected change between two profile snapshots."""

    def __init__(
        self,
        category: str,
        description: str,
        severity: str = "info",
        old_value: Optional[str] = None,
        new_value: Optional[str] = None,
    ):
        self.category = category  # role, skills, activity, headline, company, location
        self.description = description
        self.severity = severity  # info, notable, important
        self.old_value = old_value
        self.new_value = new_value
        self.detected_at = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "description": self.description,
            "severity": self.severity,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "detected_at": self.detected_at,
        }


class ChangeDetector:
    """Compares two PersonProfile states and produces change events."""

    def detect_changes(
        self,
        previous: Dict[str, Any],
        current: PersonProfile,
    ) -> List[Dict[str, Any]]:
        """Compare previous snapshot dict with current PersonProfile.

        Args:
            previous: dict of the old profile (from saved snapshot)
            current: the new PersonProfile after collection

        Returns:
            List of ChangeEvent dicts
        """
        changes: List[ChangeEvent] = []

        if not previous:
            return []

        # Role change
        old_role = previous.get("current_role", "")
        new_role = current.current_role or ""
        if old_role and new_role and old_role.lower() != new_role.lower():
            changes.append(ChangeEvent(
                category="role",
                description=f"Role changed from '{old_role}' to '{new_role}'",
                severity="important",
                old_value=old_role,
                new_value=new_role,
            ))

        # Company change
        old_company = previous.get("current_company", "")
        new_company = current.current_company or ""
        if old_company and new_company and old_company.lower() != new_company.lower():
            changes.append(ChangeEvent(
                category="company",
                description=f"Moved from {old_company} to {new_company}",
                severity="important",
                old_value=old_company,
                new_value=new_company,
            ))

        # Headline change
        old_headline = previous.get("headline", "")
        new_headline = current.headline or ""
        if old_headline and new_headline and old_headline != new_headline:
            changes.append(ChangeEvent(
                category="headline",
                description=f"Updated headline: '{new_headline}'",
                severity="notable",
                old_value=old_headline,
                new_value=new_headline,
            ))

        # Location change
        old_location = previous.get("location", "")
        new_location = current.location or ""
        if old_location and new_location and old_location.lower() != new_location.lower():
            changes.append(ChangeEvent(
                category="location",
                description=f"Location changed from '{old_location}' to '{new_location}'",
                severity="notable",
                old_value=old_location,
                new_value=new_location,
            ))

        # New skills
        old_skills = set(s.lower() for s in previous.get("skills", []))
        new_skills = set(s.lower() for s in (current.skills or []))
        added_skills = new_skills - old_skills
        if added_skills and old_skills:  # Only flag if we had skills before
            skill_list = ", ".join(sorted(added_skills)[:5])
            changes.append(ChangeEvent(
                category="skills",
                description=f"New skills added: {skill_list}",
                severity="info",
                new_value=skill_list,
            ))

        # New LinkedIn posts (count difference)
        old_post_count = len(previous.get("linkedin_posts", []))
        new_post_count = len(current.linkedin_posts or [])
        if new_post_count > old_post_count and old_post_count > 0:
            diff = new_post_count - old_post_count
            changes.append(ChangeEvent(
                category="activity",
                description=f"{diff} new LinkedIn post{'s' if diff > 1 else ''} since last collection",
                severity="info",
            ))

        # New tweets (count difference)
        old_tweet_count = len(previous.get("tweets", []))
        new_tweet_count = len(current.tweets or [])
        if new_tweet_count > old_tweet_count and old_tweet_count > 0:
            diff = new_tweet_count - old_tweet_count
            changes.append(ChangeEvent(
                category="activity",
                description=f"{diff} new tweet{'s' if diff > 1 else ''} since last collection",
                severity="info",
            ))

        if changes:
            logger.info(
                f"[ChangeDetector] {current.name}: {len(changes)} changes detected"
            )

        return [c.to_dict() for c in changes]

    def take_snapshot(self, person: PersonProfile) -> Dict[str, Any]:
        """Create a snapshot dict from a PersonProfile for future comparison."""
        return {
            "snapshot_at": datetime.utcnow().isoformat(),
            "name": person.name,
            "headline": person.headline,
            "current_role": person.current_role,
            "current_company": person.current_company,
            "location": person.location,
            "bio": person.bio,
            "skills": list(person.skills or []),
            "linkedin_posts": [
                {"text": p.text[:200]} for p in (person.linkedin_posts or [])
            ],
            "tweets": [
                {"text": t.text[:200]} for t in (person.tweets or [])
            ],
            "tags": list(person.tags or []),
        }


# Singleton
change_detector = ChangeDetector()
