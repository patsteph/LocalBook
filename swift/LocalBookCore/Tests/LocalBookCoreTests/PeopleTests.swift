import Foundation
import Testing
@testable import LocalBookCore

@Suite struct PeopleModelTests {
    /// An old `people_config` (snake_case, naive ISO dates, github_activity as a dict) reads, and coaching switches
    /// itself on when anyone has notes.
    @Test func readsAnOldConfig() throws {
        let json = """
        {"notebook_id": "nb1", "notebook_name": "Team", "team_name": "Platform", "coaching_enabled": false, "collection_schedule": "weekly",
         "members": [{"id": "p1", "notebook_id": "nb1", "name": "Rosa Alvarez", "headline": "Founder", "social_links": {"github": "https://github.com/rosa", "personal_site": "https://rosa.example"},
                      "current_role": "CEO", "current_company": "Harbor Lights", "skills": ["roasting"],
                      "github_activity": {"pinned_repos": [{"name": "roastlog", "description": "roast curves", "language": "Swift"}], "contributions_text": "412 contributions in the last year"},
                      "coaching_notes": [{"id": "n1", "text": "grade 12", "category": "observation", "created_at": "2026-11-01T10:00:00.123456"}],
                      "goals": [{"id": "g1", "goal": "Open Denver", "status": "active", "created_at": "2026-10-01T09:00:00"}],
                      "coaching_insights": {"strengths": ["Ships"], "growth_areas": [], "overall_summary": "Good."},
                      "created_at": "2026-09-01T00:00:00", "updated_at": "2026-11-01T00:00:00"}],
         "created_at": "2026-09-01T00:00:00", "updated_at": "2026-11-01T00:00:00"}
        """
        let c = try JSONDecoder().decode(PeopleConfig.self, from: Data(json.utf8))
        #expect(c.teamName == "Platform" && c.coachingEnabled == true && c.collectionSchedule == .weekly)
        let p = try #require(c.members.first)
        #expect(p.name == "Rosa Alvarez" && p.roleLine == "CEO at Harbor Lights")
        #expect(p.githubRepos == ["roastlog: roast curves: [Swift]"] && p.githubContributions == "412 contributions in the last year")
        #expect(p.coachingNotes.first?.category == .observation)
        #expect(p.coachingNotes.first.map { Calendar(identifier: .gregorian).component(.year, from: $0.createdAt) } == 2026)
        #expect(p.goals.first?.status == .active && p.coachingInsights?.strengths == ["Ships"])
        #expect(p.links.map(\.platform) == [.github, .personalSite])
        // Round trip keeps the old key names.
        let again = try JSONDecoder().decode(PeopleConfig.self, from: JSONEncoder().encode(c))
        #expect(again.members.first?.githubContributions == p.githubContributions && again.members.first?.coachingNotes.first?.id == "n1")
        let text = String(decoding: try JSONEncoder().encode(c), as: UTF8.self)
        #expect(text.contains("\"coaching_notes\"") && text.contains("\"social_links\"") && text.contains("\"github_activity\""))
    }

    @Test func platformsFromAddresses() {
        #expect(SocialPlatform.detect("https://www.linkedin.com/in/rosa") == .linkedin)
        #expect(SocialPlatform.detect("https://x.com/rosa") == .twitter && SocialPlatform.detect("twitter.com/rosa") == .twitter)
        #expect(SocialPlatform.detect("https://github.com/rosa") == .github)
        #expect(SocialPlatform.detect("https://rosa.example/about") == .personalSite)
        #expect(SocialPlatform.linkedin.needsSignIn && !SocialPlatform.github.needsSignIn && !SocialPlatform.personalSite.needsSignIn)
    }

    @Test func scheduleDue() {
        var c = PeopleConfig(notebookId: "nb", members: [PersonProfile(notebookId: "nb", name: "A")], collectionSchedule: .weekly)
        #expect(c.isDue())                                   // never run
        c.lastCollectionRun = Date().addingTimeInterval(-3 * 86_400)
        #expect(!c.isDue())
        c.lastCollectionRun = Date().addingTimeInterval(-8 * 86_400)
        #expect(c.isDue())
        c.collectionSchedule = .manual
        #expect(!c.isDue())
        #expect(!PeopleConfig(notebookId: "nb").isDue())      // nobody to read
    }
}

@Suite struct ActivityAnalysisTests {
    func post(_ daysAgo: Double, _ text: String = "post", url: String = "", shares: Int = 0) -> SocialPost {
        SocialPost(platform: "linkedin", text: text, date: PeopleDates.string(Date().addingTimeInterval(-daysAgo * 86_400)), url: url, shares: shares)
    }

    @Test func frequencyFromGaps() {
        #expect(ActivityAnalysis.classifyFrequency([]) == "inactive")
        #expect(ActivityAnalysis.classifyFrequency([post(3)]) == "monthly")
        #expect(ActivityAnalysis.classifyFrequency([post(60)]) == "rarely")
        #expect(ActivityAnalysis.classifyFrequency([post(200)]) == "inactive")
        #expect(ActivityAnalysis.classifyFrequency([post(0), post(1), post(2), post(3)]) == "daily")
        #expect(ActivityAnalysis.classifyFrequency([post(0), post(7), post(14)]) == "weekly")
        #expect(ActivityAnalysis.classifyFrequency([post(0), post(30), post(60)]) == "monthly")
        #expect(ActivityAnalysis.classifyFrequency([post(0), post(300)]) == "inactive")
    }

    @Test func contentTypes() {
        #expect(ActivityAnalysis.contentType(post(0, "Merged pull request #4"), platform: "github") == "pull_request")
        #expect(ActivityAnalysis.contentType(post(0, "Release 2.0"), platform: "github") == "release")
        #expect(ActivityAnalysis.contentType(post(0, "fix typo"), platform: "github") == "commit")
        #expect(ActivityAnalysis.contentType(post(0, "Read this", url: "https://linkedin.com/pulse/x"), platform: "linkedin") == "article")
        #expect(ActivityAnalysis.contentType(post(0, "Great point", shares: 3), platform: "linkedin") == "share")
        #expect(ActivityAnalysis.contentType(post(0, "@rosa yes"), platform: "twitter") == "comment")
        #expect(ActivityAnalysis.contentType(post(0, String(repeating: "word ", count: 120)), platform: "linkedin") == "article")
        #expect(ActivityAnalysis.contentType(post(0, "hello #coffee"), platform: "linkedin") == "post")
    }

    @Test func wholeProfile() {
        var p = PersonProfile(notebookId: "nb", name: "Rosa", socialLinks: ["linkedin": "https://linkedin.com/in/rosa", "github": "https://github.com/rosa", "personal_site": "https://rosa.example"])
        p.linkedinPosts = [post(1, "Denver roastery opens in December"), post(8, "Shipping credit math"), post(15, "Hiring regional managers")]
        p.githubRepos = ["roastlog: roast curves [Swift]"]
        p.githubContributions = "1,204 contributions in the last year"
        let a = ActivityAnalysis.analyze(p)
        #expect(a.platformFrequency["linkedin"] == "weekly" && a.platformFrequency["github"] == "daily" && a.platformFrequency["personal_site"] == "inactive")
        #expect(a.overallFrequency == "daily")
        #expect(a.recentItems.filter { $0.platform == "linkedin" }.count == 3 && a.recentItems.first { $0.platform == "github" }?.contentType == "repository")
        #expect(a.platformLastActive["linkedin"]?.isEmpty == false && a.overallLastActive == a.platformLastActive["linkedin"])
        #expect(a.topics.contains("denver") || a.topics.contains("roastery"))
        #expect(ActivityAnalysis.contributionsCount("1,204 contributions in the last year") == 1204 && ActivityAnalysis.contributionsCount("") == 0)
        #expect(ActivityAnalysis.overall(["a": "rarely", "b": "weekly"]) == "weekly" && ActivityAnalysis.overall([:]) == "inactive")
    }
}

@Suite struct ProfileChangeTests {
    @Test func detectsMoves() {
        var before = PersonProfile(notebookId: "nb", name: "Rosa", currentRole: "CEO", currentCompany: "Harbor Lights")
        before.headline = "Founder"; before.location = "Portland"; before.skills = ["roasting", "hiring"]
        before.linkedinPosts = [SocialPost(platform: "linkedin", text: "a")]
        let snap = ProfileChanges.Snapshot(before)
        var after = before
        after.currentRole = "Executive Chair"; after.currentCompany = "Harbor Lights Group"; after.location = "Denver"
        after.skills += ["public speaking"]; after.linkedinPosts += [SocialPost(platform: "linkedin", text: "b"), SocialPost(platform: "linkedin", text: "c")]
        let changes = ProfileChanges.detect(previous: snap, current: after)
        #expect(changes.map(\.category) == ["role", "company", "location", "skills", "activity"])
        #expect(changes[0].severity == "important" && changes[0].oldValue == "CEO" && changes[0].newValue == "Executive Chair")
        #expect(changes[3].newValue == "public speaking")
        #expect(changes[4].description == "2 new LinkedIn posts since the last read")
        // No baseline, no changes; same values, no changes; case-only difference, no change.
        #expect(ProfileChanges.detect(previous: nil, current: after).isEmpty)
        #expect(ProfileChanges.detect(previous: snap, current: before).isEmpty)
        var cased = before; cased.currentRole = "ceo"
        #expect(ProfileChanges.detect(previous: snap, current: cased).isEmpty)
        // Skills only count once there were skills before.
        var fresh = before; fresh.skills = []
        var learned = fresh; learned.skills = ["x"]
        #expect(ProfileChanges.detect(previous: ProfileChanges.Snapshot(fresh), current: learned).isEmpty)
    }
}

@Suite struct PeoplePromptTests {
    @Test func readsExtractedJSON() throws {
        let reply = """
        Here you go:
        {"name": "Rosa Alvarez", "headline": "Founder and CEO", "bio": null, "location": "Portland, Oregon", "current_role": "CEO", "current_company": "Harbor Lights Coffee",
         "skills": ["roasting", "hiring"], "experience": [{"title": "Founder and CEO", "company": "Harbor Lights Coffee", "dates": "2014 - present", "is_current": true}],
         "education": [], "recent_posts": [{"text": "Why Denver, and why now", "date": "2026-11-20"}], "repositories": null, "contributions": null, "recent_topics": ["coffee"]}
        """
        let e = try #require(PeoplePrompts.readExtracted(reply))
        #expect(e.name == "Rosa Alvarez" && e.current_company == "Harbor Lights Coffee" && e.experience?.first?.isCurrent == true)
        #expect(e.filled.contains("skills") && e.filled.contains("recent_posts") && !e.filled.contains("bio"))
        var p = PersonProfile(notebookId: "nb", name: "")
        PeoplePrompts.merge(e, from: .personalSite, into: &p)
        #expect(p.name == "Rosa Alvarez" && p.currentRole == "CEO" && p.skills == ["roasting", "hiring"] && p.blogPosts.count == 1 && p.blogPosts[0].date == "2026-11-20")
        #expect(p.activityProfile.topics == ["coffee"])
        // A second page does not overwrite identity but adds skills; LinkedIn owns experience.
        var e2 = PeoplePrompts.Extracted(); e2.name = "R. Alvarez"; e2.current_role = "Chair"; e2.skills = ["Roasting", "cupping"]
        e2.experience = [WorkExperience(title: "Chair", company: "HL")]
        PeoplePrompts.merge(e2, from: .linkedin, into: &p)
        #expect(p.name == "Rosa Alvarez" && p.currentRole == "CEO" && p.skills == ["roasting", "hiring", "cupping"] && p.experience.first?.title == "Chair")
        // Lists that came back as a string still read.
        let loose = PeoplePrompts.readExtracted("{\"name\": \"X\", \"skills\": \"roasting\", \"recent_posts\": [\"a post\"], \"company\": \"HL\"}")
        #expect(loose?.skills == ["roasting"] && loose?.recent_posts?.first?.text == "a post" && loose?.current_company == "HL")
        #expect(PeoplePrompts.readExtracted("no json here") == nil && PeoplePrompts.readExtracted(nil) == nil)
    }

    @Test func coachingPromptAndInsights() throws {
        var p = PersonProfile(notebookId: "nb", name: "Rosa", currentRole: "CEO", currentCompany: "Harbor Lights")
        p.bio = "Founder."; p.skills = ["roasting"]
        p.coachingNotes = [CoachingNote(text: "grade 12", category: .observation, createdAt: Date(timeIntervalSince1970: 1_700_000_000)), CoachingNote(text: "earlier", createdAt: Date(timeIntervalSince1970: 1_600_000_000))]
        p.goals = [CoachingGoal(goal: "Open Denver")]
        var ctx = PeoplePrompts.Context()
        ctx.passages = [(date: "2026-02-01", source: "expectations.pdf", text: "Grade 12 leads a region.")]
        ctx.topics = [(name: "Expansion", count: 12, keywords: ["denver", "roastery"])]
        ctx.findings = [(kind: "answer", title: "Who runs Mountain?")]
        ctx.questions = ["What did Rosa say about shipping credits?"]
        let prompt = PeoplePrompts.coachingPrompt(p, context: ctx)
        #expect(prompt.contains("Name: Rosa") && prompt.contains("Role: CEO at Harbor Lights"))
        #expect(prompt.contains("- Open Denver [active]"))
        let earlier = try #require(prompt.range(of: "[General] (2020-09-13) earlier")), later = try #require(prompt.range(of: "[Observation] (2023-11-14) grade 12"))
        #expect(earlier.lowerBound < later.lowerBound)                        // chronological
        #expect(prompt.contains("[2026-02-01 — expectations.pdf] Grade 12 leads a region."))
        #expect(prompt.contains("- Expansion (12 passages): denver, roastery") && prompt.contains("- [answer] Who runs Mountain?") && prompt.contains("- What did Rosa say about shipping credits?"))
        #expect(prompt.contains("CRITICAL — Grade/Level Calibration"))
        #expect(PeoplePrompts.hasEnough(p))
        #expect(!PeoplePrompts.hasEnough(PersonProfile(notebookId: "nb", name: "Nobody")))

        let ci = try #require(PeoplePrompts.readInsights("""
        {"strengths": ["Opened three roasteries"], "growth_areas": [], "conversation_starters": ["Denver launch"], "topic_trends": "expansion", "goal_observations": [], "overall_summary": "Steady."}
        """))
        #expect(ci.strengths == ["Opened three roasteries"] && ci.topicTrends == ["expansion"] && ci.overallSummary == "Steady." && ci.generatedAt != nil)
        #expect(PeoplePrompts.readInsights("{}") == nil && PeoplePrompts.readInsights(nil) == nil)
    }

    @Test func notebookText() {
        var p = PersonProfile(notebookId: "nb", name: "Rosa", currentRole: "CEO", currentCompany: "Harbor Lights", tags: ["founder"])
        p.bio = "Founder."; p.experience = [WorkExperience(title: "CEO", company: "Harbor Lights", dates: "2014 - present")]
        p.skills = ["roasting"]; p.githubRepos = ["roastlog: curves [Swift]"]; p.githubContributions = "412 contributions"
        p.coachingInsights = CoachingInsights(strengths: ["Ships"], overallSummary: "Good.")
        p.goals = [CoachingGoal(goal: "Open Denver")]
        let text = ProfileText.profile(p)
        #expect(text.hasPrefix("# Profile: Rosa\nCurrent Role: CEO at Harbor Lights\nTags: founder"))
        #expect(text.contains("## Work Experience\n- CEO at Harbor Lights (2014 - present)"))
        #expect(text.contains("## GitHub Activity\n- roastlog: curves [Swift]\nContributions: 412 contributions"))
        #expect(text.contains("## Coaching Insights\nGood.\nStrengths: Ships") && text.contains("## Goals\n- Open Denver [active]"))
        #expect(ProfileText.notes(p).isEmpty)
        p.coachingNotes = [CoachingNote(text: "Sharp.", category: .strength, createdAt: Date(timeIntervalSince1970: 1_700_000_000))]
        #expect(ProfileText.notes(p) == "# Coaching Notes: Rosa\n\n\n## [Strength] — 2023-11-14\nSharp.")
        #expect(p.profileSourceId == "people-profile-" + p.id && p.notesSourceId == "people-notes-" + p.id)
    }

    @Test func oldDatesRead() {
        #expect(PeopleDates.date("2026-11-01T10:00:00.123456") != nil)
        #expect(PeopleDates.date("2026-11-01T10:00:00") != nil)
        #expect(PeopleDates.date("2026-11-01T10:00:00Z") != nil)
        #expect(PeopleDates.date("2026-11-01") != nil)
        #expect(PeopleDates.date("") == nil && PeopleDates.date(nil) == nil && PeopleDates.date("yesterday") == nil)
    }
}
