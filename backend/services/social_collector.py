"""Social Collector Service — Headless Playwright profile collection engine.

Collects social media profile data using saved encrypted sessions.
Decrypts session state in memory, launches headless Chromium, extracts
structured data via platform-specific DOM selectors with LLM fallback.

Security:
- Session state decrypted in memory only, never written as plain text
- Decrypted bytes zeroed after use
- Respectful rate limiting between requests
"""

import os
import json
import random
import asyncio
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

from config import settings
from models.person_profile import PersonProfile, SocialPlatform, ProfileSource

logger = logging.getLogger(__name__)


def _ensure_playwright_browsers_path():
    """Point Playwright at the system browser cache instead of the PyInstaller bundle."""
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    system_cache = Path.home() / "Library" / "Caches" / "ms-playwright"
    if system_cache.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(system_cache)
        return
    xdg_cache = Path.home() / ".cache" / "ms-playwright"
    if xdg_cache.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(xdg_cache)
        return


# Rate limiting: random delay between profile visits
MIN_DELAY_SECONDS = 2
MAX_DELAY_SECONDS = 5


class SocialCollectorService:
    """Collects social profile data via headless Playwright."""

    def __init__(self):
        self._playwright_available = None

    def _check_playwright(self):
        if self._playwright_available is None:
            _ensure_playwright_browsers_path()
            try:
                import playwright.async_api
                self._playwright_available = True
            except ImportError:
                self._playwright_available = False
        if not self._playwright_available:
            raise RuntimeError(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            )

    async def collect_person(
        self, person: PersonProfile, notebook_id: str
    ) -> Dict[str, Any]:
        """Collect data from all configured social platforms for a person."""
        self._check_playwright()

        # Take snapshot before collection for change detection
        from services.change_detector import change_detector
        previous_snapshot = change_detector.take_snapshot(person)

        results = {
            "person_id": person.id,
            "name": person.name,
            "platforms_collected": [],
            "errors": [],
            "data": {},
        }

        for platform_key, url in person.social_links.items():
            if not url:
                continue

            try:
                platform_data = await self._collect_platform(platform_key, url)
                if platform_data:
                    results["platforms_collected"].append(platform_key)
                    results["data"][platform_key] = platform_data

                    # Update the person profile with collected data
                    self._merge_platform_data(person, platform_key, platform_data)

                    # Record source
                    person.sources.append(ProfileSource(
                        platform=platform_key,
                        url=url,
                        data_fields=list(platform_data.keys()),
                        success=True,
                    ))
                    person.last_collected[platform_key] = datetime.utcnow().isoformat()

            except Exception as e:
                error_msg = f"{platform_key}: {str(e)}"
                logger.error(f"Collection failed for {person.name} on {platform_key}: {e}")
                results["errors"].append(error_msg)
                person.sources.append(ProfileSource(
                    platform=platform_key,
                    url=url,
                    success=False,
                    error=str(e),
                ))

            # Rate limiting between platforms
            delay = random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
            await asyncio.sleep(delay)

        person.updated_at = datetime.utcnow().isoformat()

        # Save updated profile back to config
        self._save_person_to_config(person, notebook_id)

        # Phase 4: Post-collection intelligence pipeline (fire-and-forget)
        if results["platforms_collected"]:
            asyncio.create_task(
                self._run_intelligence_pipeline(person, notebook_id, previous_snapshot)
            )

        return results

    async def collect_all(
        self, members: List[PersonProfile], notebook_id: str
    ) -> List[Dict[str, Any]]:
        """Collect data for all team members (sequentially with delays)."""
        results = []
        for member in members:
            if not member.social_links:
                continue
            result = await self.collect_person(member, notebook_id)
            results.append(result)
            # Longer delay between members
            await asyncio.sleep(random.uniform(3, 8))
        return results

    async def _collect_platform(
        self, platform_key: str, url: str
    ) -> Optional[Dict[str, Any]]:
        """Collect data from a single platform URL."""
        from playwright.async_api import async_playwright

        # Determine if we need auth
        needs_auth = platform_key in (
            SocialPlatform.LINKEDIN.value,
            SocialPlatform.TWITTER.value,
            SocialPlatform.INSTAGRAM.value,
        )

        # Load session state if needed
        session_state = None
        if needs_auth:
            from services.social_auth import social_auth
            session_state = social_auth.load_session_state(platform_key)
            if session_state is None:
                logger.warning(f"No auth session for {platform_key}, skipping")
                return None

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context_opts = {
                    "viewport": {"width": 1280, "height": 900},
                    "user_agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                }

                if session_state:
                    context_opts["storage_state"] = session_state

                context = await browser.new_context(**context_opts)
                page = await context.new_page()

                logger.info(f"Navigating to {url} for {platform_key}...")
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=30000)
                except Exception:
                    # LinkedIn and other heavy sites may never reach networkidle
                    # — the DOM content we need is usually loaded by now
                    logger.info(f"networkidle timeout for {platform_key}, proceeding with extraction")
                    await page.wait_for_timeout(2000)  # brief extra settle time

                # Platform-specific extraction
                if platform_key == SocialPlatform.LINKEDIN.value:
                    data = await self._extract_linkedin(page)
                elif platform_key == SocialPlatform.TWITTER.value:
                    data = await self._extract_twitter(page)
                elif platform_key == SocialPlatform.GITHUB.value:
                    data = await self._extract_github(page)
                elif platform_key == SocialPlatform.INSTAGRAM.value:
                    data = await self._extract_instagram(page)
                elif platform_key == SocialPlatform.PERSONAL_SITE.value:
                    data = await self._extract_generic(page, url)
                else:
                    data = await self._extract_generic(page, url)

                await context.close()
                await browser.close()

                # If platform extractors returned little data, try LLM fallback
                if data and len([v for v in data.values() if v]) < 2:
                    logger.info(f"Sparse data from {platform_key}, trying LLM extraction...")
                    page_text = data.get("_raw_text", "")
                    if page_text:
                        llm_data = await self._llm_extract(page_text, platform_key)
                        if llm_data:
                            data.update(llm_data)

                # Remove internal fields
                data.pop("_raw_text", None)

                return data if data else None

        except Exception as e:
            logger.error(f"Playwright collection error for {platform_key}: {e}")
            raise

    # =========================================================================
    # Platform-Specific Extractors
    # =========================================================================

    async def _extract_linkedin(self, page) -> Dict[str, Any]:
        """Extract data from a LinkedIn profile page."""
        data = {}
        try:
            # Wait for main content
            await page.wait_for_selector("main", timeout=10000)

            data["name"] = await self._safe_text(page, "h1")
            data["headline"] = await self._safe_text(
                page, "div.text-body-medium.break-words"
            )
            data["location"] = await self._safe_text(
                page, "span.text-body-small.inline"
            )

            # About section
            about = await self._safe_text(
                page, "#about ~ div.display-flex div.inline-show-more-text"
            )
            if not about:
                about = await self._safe_text(page, "section:has(#about) div.inline-show-more-text")
            data["about"] = about

            # Experience
            experience_items = await page.query_selector_all(
                "#experience ~ div ul > li"
            )
            experiences = []
            for item in experience_items[:5]:
                title = await self._safe_inner_text(item, "span[aria-hidden='true']")
                if title:
                    experiences.append({"title": title})
            data["experience"] = experiences

            # Skills
            skill_elements = await page.query_selector_all(
                "#skills ~ div span.hoverable-link-text"
            )
            skills = []
            for el in skill_elements[:15]:
                text = await el.inner_text()
                if text.strip():
                    skills.append(text.strip())
            data["skills"] = skills

            # Get raw text for LLM fallback
            main = await page.query_selector("main")
            if main:
                data["_raw_text"] = (await main.inner_text())[:5000]

        except Exception as e:
            logger.warning(f"LinkedIn extraction partial failure: {e}")

        return data

    async def _extract_twitter(self, page) -> Dict[str, Any]:
        """Extract data from a Twitter/X profile page."""
        data = {}
        try:
            await page.wait_for_selector(
                "[data-testid='primaryColumn']", timeout=10000
            )

            data["name"] = await self._safe_text(
                page, "[data-testid='UserName'] span:first-child"
            )
            data["handle"] = await self._safe_text(
                page, "[data-testid='UserName'] div:nth-child(2) span"
            )
            data["bio"] = await self._safe_text(
                page, "[data-testid='UserDescription']"
            )
            data["location"] = await self._safe_text(
                page, "[data-testid='UserLocation'] span:last-child"
            )
            data["website"] = await self._safe_text(
                page, "[data-testid='UserUrl'] a"
            )

            # Recent tweets
            tweet_elements = await page.query_selector_all(
                "[data-testid='tweet'] [data-testid='tweetText']"
            )
            tweets = []
            for el in tweet_elements[:10]:
                text = await el.inner_text()
                if text.strip():
                    tweets.append({"text": text.strip()})
            data["recent_tweets"] = tweets

            # Raw text fallback
            primary = await page.query_selector("[data-testid='primaryColumn']")
            if primary:
                data["_raw_text"] = (await primary.inner_text())[:5000]

        except Exception as e:
            logger.warning(f"Twitter extraction partial failure: {e}")

        return data

    async def _extract_github(self, page) -> Dict[str, Any]:
        """Extract data from a GitHub profile page. No auth needed."""
        data = {}
        try:
            await page.wait_for_selector("[itemtype='http://schema.org/Person']", timeout=10000)

            data["name"] = await self._safe_text(page, "span.p-name")
            data["bio"] = await self._safe_text(page, "div.p-note div.user-profile-bio")
            data["company"] = await self._safe_text(page, "span.p-org")
            data["location"] = await self._safe_text(page, "span.p-label")

            # Pinned repos
            pinned = await page.query_selector_all("div.pinned-item-list-item-content")
            repos = []
            for item in pinned[:6]:
                repo_name = await self._safe_inner_text(item, "span.repo")
                repo_desc = await self._safe_inner_text(item, "p.pinned-item-desc")
                lang = await self._safe_inner_text(item, "span[itemprop='programmingLanguage']")
                if repo_name:
                    repos.append({
                        "name": repo_name.strip(),
                        "description": (repo_desc or "").strip(),
                        "language": (lang or "").strip(),
                    })
            data["pinned_repos"] = repos

            # Contribution count
            contrib_text = await self._safe_text(page, "div.js-yearly-contributions h2")
            data["contributions_text"] = (contrib_text or "").strip()

            # Raw text
            profile_area = await page.query_selector("[itemtype='http://schema.org/Person']")
            if profile_area:
                data["_raw_text"] = (await profile_area.inner_text())[:3000]

        except Exception as e:
            logger.warning(f"GitHub extraction partial failure: {e}")

        return data

    async def _extract_instagram(self, page) -> Dict[str, Any]:
        """Extract data from an Instagram profile page."""
        data = {}
        try:
            await page.wait_for_selector("header", timeout=10000)

            data["name"] = await self._safe_text(page, "header span")
            data["bio"] = await self._safe_text(page, "header section > div:nth-child(3)")

            # Stats (posts, followers, following)
            stat_elements = await page.query_selector_all("header ul li span span")
            stats = []
            for el in stat_elements[:3]:
                text = await el.inner_text()
                stats.append(text.strip())
            if len(stats) >= 3:
                data["posts_count"] = stats[0]
                data["followers"] = stats[1]
                data["following"] = stats[2]

            # Raw text
            header = await page.query_selector("header")
            if header:
                data["_raw_text"] = (await header.inner_text())[:2000]

        except Exception as e:
            logger.warning(f"Instagram extraction partial failure: {e}")

        return data

    async def _extract_generic(self, page, url: str) -> Dict[str, Any]:
        """Extract data from a generic page using trafilatura."""
        data = {}
        try:
            html = await page.content()

            # Use trafilatura for content extraction (run in thread)
            import trafilatura
            text = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: trafilatura.extract(html, include_comments=False, include_tables=True)
            )
            if text:
                data["content"] = text[:10000]
                data["_raw_text"] = text[:5000]

            data["title"] = await page.title()
            data["url"] = url

        except Exception as e:
            logger.warning(f"Generic extraction failed: {e}")

        return data

    # =========================================================================
    # LLM Fallback Extraction
    # =========================================================================

    async def _llm_extract(
        self, raw_text: str, platform: str
    ) -> Optional[Dict[str, Any]]:
        """Use LLM to extract structured data from raw page text."""
        try:
            from services.ollama_client import ollama_client

            prompt = f"""Extract structured profile information from this {platform} profile page text.
Return JSON only with these fields (use null for missing):
{{
    "name": "full name",
    "headline": "professional headline or title",
    "bio": "biography or about text",
    "location": "city/region",
    "current_role": "current job title",
    "current_company": "current employer",
    "skills": ["skill1", "skill2"],
    "recent_topics": ["topic they post about"]
}}

Profile text:
{raw_text[:3000]}"""

            response = await ollama_client.generate(
                prompt=prompt,
                system="You extract structured profile data. Respond with JSON only.",
                model=settings.ollama_fast_model,
                temperature=0.1,
                timeout=15.0,
            )

            text = response.get("response", "")
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(text[start:end])
        except Exception as e:
            logger.debug(f"LLM extraction failed: {e}")
        return None

    # =========================================================================
    # Helpers
    # =========================================================================

    async def _safe_text(self, page, selector: str) -> str:
        """Safely extract text from a CSS selector."""
        try:
            el = await page.query_selector(selector)
            if el:
                return (await el.inner_text()).strip()
        except Exception:
            pass
        return ""

    async def _safe_inner_text(self, parent, selector: str) -> str:
        """Safely extract text from a child selector."""
        try:
            el = await parent.query_selector(selector)
            if el:
                return (await el.inner_text()).strip()
        except Exception:
            pass
        return ""

    async def _run_intelligence_pipeline(
        self,
        person: PersonProfile,
        notebook_id: str,
        previous_snapshot: Dict[str, Any],
    ):
        """Post-collection intelligence: RAG indexing, insights, change detection.

        Runs as a background task after collection completes.
        """
        try:
            # 1. Index profile into RAG for chat queries
            from services.profile_indexer import profile_indexer
            await profile_indexer.index_person(notebook_id, person)

            # 2. Analyze activity (frequency, content types, recent items)
            from services.activity_analyzer import analyze_activity, generate_activity_insights
            person.activity_profile = analyze_activity(person)
            logger.info(f"[Collector] Activity profile updated for {person.name}: {person.activity_profile.overall_frequency}")

            # 2b. Generate LLM activity insights (focus_summary)
            try:
                focus = await generate_activity_insights(
                    person.name,
                    person.activity_profile,
                    person.github_activity,
                )
                if focus:
                    person.activity_profile.focus_summary = focus
                    logger.info(f"[Collector] Activity insights generated for {person.name}")
            except Exception as e:
                logger.warning(f"[Collector] Activity insights generation failed: {e}")

            self._save_person_to_config(person, notebook_id)

            # 3. Index coaching notes into RAG (if any)
            if person.coaching_notes:
                await profile_indexer.index_coaching_notes(notebook_id, person)

            # 4. Generate coaching insights via LLM (enriched with notebook sources + user profile)
            from services.coaching_insights import coaching_insight_generator
            insights = await coaching_insight_generator.generate_insights(person, notebook_id=notebook_id)
            if insights:
                person.coaching_insights = insights
                self._save_person_to_config(person, notebook_id)
                logger.info(f"[Collector] Saved coaching insights for {person.name}")

            # 5. Detect changes from previous snapshot
            from services.change_detector import change_detector
            changes = change_detector.detect_changes(previous_snapshot, person)
            if changes:
                # Store changes in profile metadata for frontend display
                if not hasattr(person, "recent_changes") or not isinstance(getattr(person, "recent_changes", None), list):
                    person.recent_changes = []
                person.recent_changes = changes + getattr(person, "recent_changes", [])
                # Keep only last 20 changes
                person.recent_changes = person.recent_changes[:20]
                self._save_person_to_config(person, notebook_id)
                logger.info(f"[Collector] {len(changes)} changes detected for {person.name}")
                
                # Wire changes into event logger for morning brief + memory consolidation
                try:
                    from services.event_logger import event_logger, EventType
                    for change in changes:
                        event_logger.log(
                            EventType.NOTE_ADDED,
                            notebook_id,
                            {"person": person.name, "change": change.get("description", ""), "category": change.get("category", ""), "severity": change.get("severity", "info")},
                        )
                except Exception:
                    pass
                
                # Wire changes into timeline as events
                try:
                    from api.timeline import _timeline_data
                    import dateparser
                    for i, change in enumerate(changes):
                        detected_at = change.get("detected_at", "")
                        parsed = dateparser.parse(detected_at) if detected_at else None
                        ts = int(parsed.timestamp()) if parsed else 0
                        event = {
                            "event_id": f"change_{person.id}_{i}_{detected_at[:10] if detected_at else 'now'}",
                            "notebook_id": notebook_id,
                            "source_id": f"person_{person.id}",
                            "date_timestamp": ts,
                            "date_string": detected_at[:10] if detected_at else "",
                            "date_type": "exact",
                            "event_text": f"{person.name}: {change.get('description', '')}",
                            "context": f"[{change.get('severity', 'info').upper()}] {change.get('category', '')}: {change.get('description', '')}",
                            "confidence": 0.9,
                            "filename": f"Profile: {person.name}",
                            "is_person_change": True,
                        }
                        if notebook_id not in _timeline_data:
                            _timeline_data[notebook_id] = []
                        _timeline_data[notebook_id].append(event)
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"[Collector] Intelligence pipeline failed for {person.name}: {e}")

    def _merge_platform_data(
        self, person: PersonProfile, platform: str, data: Dict[str, Any]
    ):
        """Merge extracted platform data into the PersonProfile."""
        if not data:
            return

        # Identity fields — use first non-empty source
        if data.get("name") and not person.name:
            person.name = data["name"]
        if data.get("headline") and not person.headline:
            person.headline = data["headline"]
        if data.get("bio") and not person.bio:
            person.bio = data["bio"]
        if data.get("about") and (not person.bio or len(data["about"]) > len(person.bio)):
            person.bio = data["about"]
        if data.get("location") and not person.location:
            person.location = data["location"]
        if data.get("current_role") and not person.current_role:
            person.current_role = data["current_role"]
        if data.get("current_company") and not person.current_company:
            person.current_company = data["current_company"]
        if data.get("company") and not person.current_company:
            person.current_company = data["company"]

        # Skills — merge
        if data.get("skills"):
            existing = set(person.skills)
            for skill in data["skills"]:
                if isinstance(skill, str) and skill not in existing:
                    person.skills.append(skill)
                    existing.add(skill)

        # Platform-specific activity
        from models.person_profile import SocialPost

        if platform == SocialPlatform.LINKEDIN.value:
            if data.get("experience"):
                from models.person_profile import WorkExperience
                person.experience = [
                    WorkExperience(**exp) if isinstance(exp, dict) else exp
                    for exp in data["experience"][:10]
                ]

        elif platform == SocialPlatform.TWITTER.value:
            if data.get("recent_tweets"):
                person.tweets = [
                    SocialPost(platform="twitter", text=t.get("text", ""))
                    for t in data["recent_tweets"]
                ]

        elif platform == SocialPlatform.GITHUB.value:
            person.github_activity = {
                k: v for k, v in data.items()
                if k not in ("name", "bio", "company", "location", "_raw_text")
            }

        elif platform == SocialPlatform.INSTAGRAM.value:
            if data.get("posts_count"):
                person.instagram_posts = []  # Will be populated with actual posts later

    def _save_person_to_config(self, person: PersonProfile, notebook_id: str):
        """Save the updated person back to the people config."""
        try:
            from api.people import _load_config, _save_config
            config = _load_config(notebook_id)
            for i, member in enumerate(config.members):
                if member.id == person.id:
                    config.members[i] = person
                    break
            _save_config(notebook_id, config)
        except Exception as e:
            logger.error(f"Failed to save updated profile for {person.name}: {e}")


# Singleton
social_collector = SocialCollectorService()
