"""Activity Analyzer — Classifies update frequency and content types per platform.

After collection, this service examines the last N items from each platform
and produces an ActivityProfile with:
- Per-platform update frequency (daily, weekly, monthly, rarely, inactive)
- Content type breakdown (article, post, commit, share, etc.)
- Recent items summary
- LLM-generated focus summary and topic extraction
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from models.person_profile import (
    PersonProfile, SocialPost, ActivityProfile, RecentActivity,
)

logger = logging.getLogger(__name__)

# How many recent items to keep per platform
RECENT_ITEMS_PER_PLATFORM = 3

# Frequency classification thresholds (based on average days between posts)
FREQUENCY_THRESHOLDS = {
    "daily": 1.5,
    "several_per_week": 4,
    "weekly": 10,
    "biweekly": 21,
    "monthly": 45,
    "rarely": 180,
    # anything above 180 days → "inactive"
}


def _classify_frequency(posts: List[SocialPost]) -> str:
    """Classify posting frequency from a list of posts."""
    if not posts:
        return "inactive"

    # Parse dates, skip unparseable
    dates = []
    for p in posts:
        if not p.date:
            continue
        try:
            dates.append(datetime.fromisoformat(p.date.replace("Z", "+00:00")))
        except (ValueError, TypeError):
            try:
                from dateutil.parser import parse as dateparse
                dates.append(dateparse(p.date))
            except Exception:
                continue

    if len(dates) < 2:
        # Only one post — check how old it is
        if dates:
            age_days = (datetime.utcnow() - dates[0].replace(tzinfo=None)).days
            if age_days > 180:
                return "inactive"
            if age_days > 45:
                return "rarely"
            return "monthly"
        return "inactive"

    dates.sort(reverse=True)

    # Average gap between consecutive posts
    gaps = []
    for i in range(len(dates) - 1):
        gap = (dates[i].replace(tzinfo=None) - dates[i + 1].replace(tzinfo=None)).days
        gaps.append(max(gap, 0))

    avg_gap = sum(gaps) / len(gaps) if gaps else 999

    for label, threshold in FREQUENCY_THRESHOLDS.items():
        if avg_gap <= threshold:
            return label

    return "inactive"


def _classify_content_type(post: SocialPost, platform: str) -> str:
    """Classify a single post's content type based on text and platform."""
    text = (post.text or "").lower()

    if platform == "github":
        if "merge" in text or "pull request" in text:
            return "pull_request"
        if "release" in text:
            return "release"
        if "issue" in text:
            return "issue"
        return "commit"

    # LinkedIn / Twitter / generic
    if post.url and ("article" in post.url or "/pulse/" in post.url):
        return "article"
    if post.shares and post.shares > 0 and len(text) < 100:
        return "share"
    if text.startswith("re:") or text.startswith("@"):
        return "comment"
    if len(text) > 500:
        return "article"
    if any(tag in text for tag in ["#", "tip:", "thread"]):
        return "post"
    return "post"


def _overall_frequency(platform_freq: Dict[str, str]) -> str:
    """Determine overall activity level from per-platform frequencies."""
    if not platform_freq:
        return "inactive"

    rank = {
        "daily": 6, "several_per_week": 5, "weekly": 4,
        "biweekly": 3, "monthly": 2, "rarely": 1, "inactive": 0,
    }
    best = max(platform_freq.values(), key=lambda v: rank.get(v, 0))
    return best


def _posts_for_platform(person: PersonProfile, platform: str) -> List[SocialPost]:
    """Get posts list for a given platform key."""
    mapping = {
        "linkedin": person.linkedin_posts,
        "twitter": person.tweets,
        "instagram": person.instagram_posts,
        "personal_site": person.blog_posts,
    }
    return mapping.get(platform, [])


def _most_recent_date(posts: List[SocialPost]) -> Optional[str]:
    """Return the most recent date string from a list of posts, or None."""
    best = None
    for p in posts:
        if not p.date:
            continue
        try:
            dt = datetime.fromisoformat(p.date.replace("Z", "+00:00")).replace(tzinfo=None)
            if best is None or dt > best:
                best = dt
        except (ValueError, TypeError):
            try:
                from dateutil.parser import parse as dateparse
                dt = dateparse(p.date).replace(tzinfo=None)
                if best is None or dt > best:
                    best = dt
            except Exception:
                continue
    return best.isoformat() if best else None


def analyze_activity(person: PersonProfile) -> ActivityProfile:
    """Build an ActivityProfile from a person's collected social data.

    This is a fast, local analysis (no LLM needed). The LLM focus summary
    is generated separately via the coaching insights pipeline.
    """
    platform_frequency: Dict[str, str] = {}
    content_types: Dict[str, List[str]] = {}
    recent_items: List[RecentActivity] = []
    platform_last_active: Dict[str, str] = {}

    # Platforms to analyze
    platforms = list(person.social_links.keys())
    if not platforms:
        platforms = ["linkedin", "twitter", "github", "instagram", "personal_site"]

    for platform in platforms:
        url = person.social_links.get(platform, "")
        if not url and platform not in ("github",):
            continue

        if platform == "github":
            # GitHub uses a different structure
            gh = person.github_activity or {}
            repos = gh.get("recent_repos", [])
            events = gh.get("recent_events", [])
            contributions = gh.get("contributions_last_year", 0)

            if contributions and contributions > 300:
                platform_frequency["github"] = "daily"
            elif contributions and contributions > 50:
                platform_frequency["github"] = "weekly"
            elif contributions and contributions > 10:
                platform_frequency["github"] = "monthly"
            elif repos or events:
                platform_frequency["github"] = "rarely"
            else:
                platform_frequency["github"] = "inactive"

            # Derive last_active for GitHub
            gh_last = None
            for evt in (events or []):
                d = evt.get("date", "")
                if d:
                    try:
                        dt = datetime.fromisoformat(d.replace("Z", "+00:00")).replace(tzinfo=None)
                        if gh_last is None or dt > gh_last:
                            gh_last = dt
                    except Exception:
                        pass
            if gh_last is None and person.last_collected.get("github"):
                try:
                    gh_last = datetime.fromisoformat(person.last_collected["github"])
                except Exception:
                    pass
            if gh_last:
                platform_last_active["github"] = gh_last.isoformat()

            # Build recent items from events
            types_seen = set()
            for evt in (events or [])[:RECENT_ITEMS_PER_PLATFORM]:
                ctype = evt.get("type", "commit").lower()
                types_seen.add(ctype)
                recent_items.append(RecentActivity(
                    platform="github",
                    title=evt.get("title", evt.get("repo", "")),
                    summary=evt.get("description", ""),
                    url=evt.get("url", ""),
                    date=evt.get("date", ""),
                    content_type=ctype,
                ))
            content_types["github"] = list(types_seen) if types_seen else ["commit"]
            continue

        # Standard social posts
        posts = _posts_for_platform(person, platform)
        platform_frequency[platform] = _classify_frequency(posts)

        # Derive last_active for this platform
        last_dt = _most_recent_date(posts)
        if last_dt:
            platform_last_active[platform] = last_dt
        elif person.last_collected.get(platform):
            # Fall back to last collection time if we have data but no post dates
            platform_last_active[platform] = person.last_collected[platform]

        types_seen = set()
        sorted_posts = sorted(
            posts,
            key=lambda p: p.date or "",
            reverse=True,
        )

        for post in sorted_posts[:RECENT_ITEMS_PER_PLATFORM]:
            ctype = _classify_content_type(post, platform)
            types_seen.add(ctype)
            recent_items.append(RecentActivity(
                platform=platform,
                title=post.text[:80] if post.text else "",
                summary=post.text[:200] if post.text else "",
                url=post.url,
                date=post.date,
                content_type=ctype,
                engagement={
                    "likes": post.likes,
                    "comments": post.comments,
                    "shares": post.shares,
                },
            ))

        content_types[platform] = list(types_seen) if types_seen else []

    overall = _overall_frequency(platform_frequency)

    # Build topic list from recent item text (simple keyword extraction)
    topics = _extract_topics(recent_items)

    # Compute overall_last_active as the most recent across all platforms
    overall_last_active = ""
    if platform_last_active:
        try:
            best = max(
                datetime.fromisoformat(d) for d in platform_last_active.values()
            )
            overall_last_active = best.isoformat()
        except Exception:
            pass

    # Sort all recent items chronologically (oldest first) for progression
    recent_items.sort(key=lambda ri: ri.date or '')

    return ActivityProfile(
        platform_frequency=platform_frequency,
        content_types=content_types,
        recent_items=recent_items,
        overall_frequency=overall,
        platform_last_active=platform_last_active,
        overall_last_active=overall_last_active,
        topics=topics,
        last_analyzed=datetime.utcnow().isoformat(),
    )


async def generate_activity_insights(
    person_name: str,
    activity_profile: "ActivityProfile",
    github_activity: Dict = None,
) -> str:
    """Use LLM to generate a concise activity focus summary.

    Returns a 2-3 sentence insight about what the person has been focused on,
    their engagement patterns, and any notable trends.
    """
    try:
        from services.ollama_client import ollama_client
        from config import settings

        # Build context from activity data
        parts = []
        if activity_profile.platform_frequency:
            freq_str = ", ".join(
                f"{p}: {f}" for p, f in activity_profile.platform_frequency.items()
            )
            parts.append(f"Platform activity levels: {freq_str}")

        if activity_profile.topics:
            parts.append(f"Key topics: {', '.join(activity_profile.topics)}")

        if activity_profile.recent_items:
            items_str = "\n".join(
                f"- [{ri.platform}] {ri.title[:100]}"
                for ri in activity_profile.recent_items[:6]
            )
            parts.append(f"Recent activity:\n{items_str}")

        if github_activity:
            repos = github_activity.get("pinned_repos", [])
            contrib = github_activity.get("contributions_text", "")
            if repos:
                repo_str = ", ".join(r.get("name", "") for r in repos[:5])
                parts.append(f"GitHub repos: {repo_str}")
            if contrib:
                parts.append(f"GitHub: {contrib}")

        if not parts:
            return ""

        context = "\n".join(parts)

        prompt = f"""Based on the following activity data for {person_name}, write a concise 2-3 sentence insight about what they've been focused on, their engagement level, and any notable patterns.

{context}

Be specific and actionable. Write in third person. Do not use bullet points — just flowing sentences."""

        response = await ollama_client.generate(
            prompt=prompt,
            system="You are a concise professional analyst. Provide brief activity insights.",
            model=settings.ollama_fast_model,
            temperature=0.4,
        )
        return response.strip()
    except Exception as e:
        logger.warning(f"Failed to generate activity insights: {e}")
        return ""


def _extract_topics(items: List[RecentActivity], max_topics: int = 5) -> List[str]:
    """Simple topic extraction from recent activity text."""
    from collections import Counter

    # Common stop words to skip
    stop = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "into", "through", "during",
        "before", "after", "above", "below", "between", "out", "off", "over",
        "under", "again", "further", "then", "once", "here", "there", "when",
        "where", "why", "how", "all", "each", "every", "both", "few", "more",
        "most", "other", "some", "such", "no", "nor", "not", "only", "own",
        "same", "so", "than", "too", "very", "just", "about", "up", "it",
        "its", "i", "me", "my", "we", "our", "you", "your", "he", "him",
        "his", "she", "her", "they", "them", "their", "this", "that", "and",
        "but", "or", "if", "while", "what", "which", "who", "whom",
        "new", "also", "like", "get", "got", "one", "two", "us", "re",
    }

    words = Counter()
    for item in items:
        text = f"{item.title} {item.summary}".lower()
        for word in text.split():
            # Clean punctuation
            w = word.strip(".,!?;:()[]{}\"'#@-_/\\|<>")
            if len(w) > 2 and w not in stop and not w.startswith("http"):
                words[w] += 1

    return [w for w, _ in words.most_common(max_topics)]
