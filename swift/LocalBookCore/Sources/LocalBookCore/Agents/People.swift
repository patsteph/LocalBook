import Foundation

/// The People profiler: a notebook about people (a team, a cohort, people being coached). Each member has the public
/// profiles the user points at, what was read from them, the user's own private notes and goals, an activity picture,
/// the changes seen between reads, and the coaching insights the model wrote from all of it against the notebook.
/// Field names follow the old `PersonProfile` / `PeopleNotebookConfig` so an old `people_config` reads here.
public enum SocialPlatform: String, Codable, Sendable, CaseIterable, Identifiable {
    case linkedin, twitter, github, instagram, personalSite = "personal_site"
    public var id: String { rawValue }
    public var label: String {
        switch self { case .linkedin: "LinkedIn"; case .twitter: "X / Twitter"; case .github: "GitHub"; case .instagram: "Instagram"; case .personalSite: "Personal site" }
    }
    public var placeholder: String {
        switch self {
        case .linkedin: "https://linkedin.com/in/username"; case .twitter: "https://x.com/username"; case .github: "https://github.com/username"
        case .instagram: "https://instagram.com/username"; case .personalSite: "https://example.com"
        }
    }
    /// Whether the site shows a profile only to someone signed in (the old Playwright sessions).
    public var needsSignIn: Bool { self == .linkedin || self == .twitter || self == .instagram }
    public var loginURL: URL? {
        switch self {
        case .linkedin: URL(string: "https://www.linkedin.com/login")
        case .twitter: URL(string: "https://x.com/i/flow/login")
        case .instagram: URL(string: "https://www.instagram.com/accounts/login/")
        default: nil
        }
    }
    /// The cookie domains that show a sign-in happened.
    public var cookieDomains: [String] {
        switch self { case .linkedin: ["linkedin.com"]; case .twitter: ["x.com", "twitter.com"]; case .instagram: ["instagram.com"]; default: [] }
    }
    /// The platform an address belongs to; anything unknown is a personal site.
    public static func detect(_ url: String) -> SocialPlatform {
        let host = (URL(string: url)?.host() ?? url).lowercased()
        if host.contains("linkedin.com") { return .linkedin }
        if host.contains("x.com") || host.contains("twitter.com") { return .twitter }
        if host.contains("github.com") { return .github }
        if host.contains("instagram.com") { return .instagram }
        return .personalSite
    }
}

public struct WorkExperience: Codable, Sendable, Hashable {
    public var title: String
    public var company: String
    public var dates: String
    public var description: String
    public var isCurrent: Bool
    enum CodingKeys: String, CodingKey { case title, company, dates, description, isCurrent = "is_current" }
    public init(title: String = "", company: String = "", dates: String = "", description: String = "", isCurrent: Bool = false) {
        self.title = title; self.company = company; self.dates = dates; self.description = description; self.isCurrent = isCurrent
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        company = try c.decodeIfPresent(String.self, forKey: .company) ?? ""
        dates = try c.decodeIfPresent(String.self, forKey: .dates) ?? ""
        description = try c.decodeIfPresent(String.self, forKey: .description) ?? ""
        isCurrent = try c.decodeIfPresent(Bool.self, forKey: .isCurrent) ?? false
    }
}

public struct Education: Codable, Sendable, Hashable {
    public var school: String, degree: String, field: String, dates: String
    public init(school: String = "", degree: String = "", field: String = "", dates: String = "") { self.school = school; self.degree = degree; self.field = field; self.dates = dates }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        school = try c.decodeIfPresent(String.self, forKey: .school) ?? ""; degree = try c.decodeIfPresent(String.self, forKey: .degree) ?? ""
        field = try c.decodeIfPresent(String.self, forKey: .field) ?? ""; dates = try c.decodeIfPresent(String.self, forKey: .dates) ?? ""
    }
}

/// One post, tweet, commit line or article seen on a platform.
public struct SocialPost: Codable, Sendable, Hashable, Identifiable {
    public var platform: String
    public var text: String
    public var date: String
    public var url: String
    public var likes: Int, comments: Int, shares: Int
    public var id: String { platform + "|" + url + "|" + String(text.prefix(80)) }
    public init(platform: String, text: String, date: String = "", url: String = "", likes: Int = 0, comments: Int = 0, shares: Int = 0) {
        self.platform = platform; self.text = text; self.date = date; self.url = url; self.likes = likes; self.comments = comments; self.shares = shares
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        platform = try c.decodeIfPresent(String.self, forKey: .platform) ?? ""; text = try c.decodeIfPresent(String.self, forKey: .text) ?? ""
        date = try c.decodeIfPresent(String.self, forKey: .date) ?? ""; url = try c.decodeIfPresent(String.self, forKey: .url) ?? ""
        likes = try c.decodeIfPresent(Int.self, forKey: .likes) ?? 0; comments = try c.decodeIfPresent(Int.self, forKey: .comments) ?? 0; shares = try c.decodeIfPresent(Int.self, forKey: .shares) ?? 0
    }
}

public struct RecentActivity: Codable, Sendable, Hashable, Identifiable {
    public var platform: String, title: String, summary: String, url: String, date: String, contentType: String
    public var id: String { platform + "|" + url + "|" + title }
    enum CodingKeys: String, CodingKey { case platform, title, summary, url, date, contentType = "content_type" }
    public init(platform: String, title: String, summary: String = "", url: String = "", date: String = "", contentType: String = "post") {
        self.platform = platform; self.title = title; self.summary = summary; self.url = url; self.date = date; self.contentType = contentType
    }
}

/// How often someone shows up where, and what about: the old `ActivityProfile`.
public struct ActivityProfile: Codable, Sendable, Hashable {
    public var platformFrequency: [String: String]
    public var contentTypes: [String: [String]]
    public var recentItems: [RecentActivity]
    public var focusSummary: String
    public var topics: [String]
    public var platformLastActive: [String: String]
    public var overallFrequency: String
    public var overallLastActive: String
    public var lastAnalyzed: Date?
    enum CodingKeys: String, CodingKey {
        case platformFrequency = "platform_frequency", contentTypes = "content_types", recentItems = "recent_items", focusSummary = "focus_summary", topics
        case platformLastActive = "platform_last_active", overallFrequency = "overall_frequency", overallLastActive = "overall_last_active", lastAnalyzed = "last_analyzed"
    }
    public init() { platformFrequency = [:]; contentTypes = [:]; recentItems = []; focusSummary = ""; topics = []; platformLastActive = [:]; overallFrequency = ""; overallLastActive = "" }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        platformFrequency = try c.decodeIfPresent([String: String].self, forKey: .platformFrequency) ?? [:]
        contentTypes = try c.decodeIfPresent([String: [String]].self, forKey: .contentTypes) ?? [:]
        recentItems = try c.decodeIfPresent([RecentActivity].self, forKey: .recentItems) ?? []
        focusSummary = try c.decodeIfPresent(String.self, forKey: .focusSummary) ?? ""
        topics = try c.decodeIfPresent([String].self, forKey: .topics) ?? []
        platformLastActive = try c.decodeIfPresent([String: String].self, forKey: .platformLastActive) ?? [:]
        overallFrequency = try c.decodeIfPresent(String.self, forKey: .overallFrequency) ?? ""
        overallLastActive = try c.decodeIfPresent(String.self, forKey: .overallLastActive) ?? ""
        lastAnalyzed = PeopleDates.date(try? c.decodeIfPresent(String.self, forKey: .lastAnalyzed))
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(platformFrequency, forKey: .platformFrequency); try c.encode(contentTypes, forKey: .contentTypes); try c.encode(recentItems, forKey: .recentItems)
        try c.encode(focusSummary, forKey: .focusSummary); try c.encode(topics, forKey: .topics); try c.encode(platformLastActive, forKey: .platformLastActive)
        try c.encode(overallFrequency, forKey: .overallFrequency); try c.encode(overallLastActive, forKey: .overallLastActive)
        try c.encodeIfPresent(lastAnalyzed.map(PeopleDates.string), forKey: .lastAnalyzed)
    }
}

/// A private note by the user about the person: never read by anything outside this Mac.
public struct CoachingNote: Codable, Sendable, Hashable, Identifiable {
    public enum Category: String, Codable, Sendable, CaseIterable { case general, strength, growthArea = "growth_area", goal, observation
        public var label: String { switch self { case .general: "General"; case .strength: "Strength"; case .growthArea: "Growth area"; case .goal: "Goal"; case .observation: "Observation" } }
    }
    public var id: String
    public var text: String
    public var category: Category
    public var createdAt: Date
    public var updatedAt: Date?
    enum CodingKeys: String, CodingKey { case id, text, category, createdAt = "created_at", updatedAt = "updated_at" }
    public init(id: String = String(UUID().uuidString.lowercased().prefix(8)), text: String, category: Category = .general, createdAt: Date = Date()) {
        self.id = id; self.text = text; self.category = category; self.createdAt = createdAt
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(String.self, forKey: .id) ?? String(UUID().uuidString.lowercased().prefix(8))
        text = try c.decodeIfPresent(String.self, forKey: .text) ?? ""
        category = Category(rawValue: try c.decodeIfPresent(String.self, forKey: .category) ?? "") ?? .general
        createdAt = PeopleDates.date(try? c.decodeIfPresent(String.self, forKey: .createdAt)) ?? Date()
        updatedAt = PeopleDates.date(try? c.decodeIfPresent(String.self, forKey: .updatedAt))
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id); try c.encode(text, forKey: .text); try c.encode(category, forKey: .category)
        try c.encode(PeopleDates.string(createdAt), forKey: .createdAt); try c.encodeIfPresent(updatedAt.map(PeopleDates.string), forKey: .updatedAt)
    }
}

public struct CoachingGoal: Codable, Sendable, Hashable, Identifiable {
    public enum Status: String, Codable, Sendable, CaseIterable { case active, completed, paused, dropped }
    public var id: String
    public var goal: String
    public var targetDate: String?
    public var status: Status
    public var notes: String
    public var createdAt: Date
    enum CodingKeys: String, CodingKey { case id, goal, targetDate = "target_date", status, notes, createdAt = "created_at" }
    public init(id: String = String(UUID().uuidString.lowercased().prefix(8)), goal: String, targetDate: String? = nil, status: Status = .active, notes: String = "", createdAt: Date = Date()) {
        self.id = id; self.goal = goal; self.targetDate = targetDate; self.status = status; self.notes = notes; self.createdAt = createdAt
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(String.self, forKey: .id) ?? String(UUID().uuidString.lowercased().prefix(8))
        goal = try c.decodeIfPresent(String.self, forKey: .goal) ?? ""
        targetDate = try c.decodeIfPresent(String.self, forKey: .targetDate)
        status = Status(rawValue: try c.decodeIfPresent(String.self, forKey: .status) ?? "") ?? .active
        notes = try c.decodeIfPresent(String.self, forKey: .notes) ?? ""
        createdAt = PeopleDates.date(try? c.decodeIfPresent(String.self, forKey: .createdAt)) ?? Date()
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id); try c.encode(goal, forKey: .goal); try c.encodeIfPresent(targetDate, forKey: .targetDate)
        try c.encode(status, forKey: .status); try c.encode(notes, forKey: .notes); try c.encode(PeopleDates.string(createdAt), forKey: .createdAt)
    }
}

/// Where a read came from and whether it worked.
public struct ProfileSource: Codable, Sendable, Hashable, Identifiable {
    public var platform: String
    public var url: String
    public var capturedAt: Date
    public var dataFields: [String]
    public var success: Bool
    public var error: String?
    public var id: String { platform + "|" + PeopleDates.string(capturedAt) }
    enum CodingKeys: String, CodingKey { case platform, url, capturedAt = "captured_at", dataFields = "data_fields", success, error }
    public init(platform: String, url: String, capturedAt: Date = Date(), dataFields: [String] = [], success: Bool = true, error: String? = nil) {
        self.platform = platform; self.url = url; self.capturedAt = capturedAt; self.dataFields = dataFields; self.success = success; self.error = error
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        platform = try c.decodeIfPresent(String.self, forKey: .platform) ?? ""; url = try c.decodeIfPresent(String.self, forKey: .url) ?? ""
        capturedAt = PeopleDates.date(try? c.decodeIfPresent(String.self, forKey: .capturedAt)) ?? Date()
        dataFields = try c.decodeIfPresent([String].self, forKey: .dataFields) ?? []
        success = try c.decodeIfPresent(Bool.self, forKey: .success) ?? true; error = try c.decodeIfPresent(String.self, forKey: .error)
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(platform, forKey: .platform); try c.encode(url, forKey: .url); try c.encode(PeopleDates.string(capturedAt), forKey: .capturedAt)
        try c.encode(dataFields, forKey: .dataFields); try c.encode(success, forKey: .success); try c.encodeIfPresent(error, forKey: .error)
    }
}

/// A change between two reads of a profile (the old `ChangeEvent`).
public struct ProfileChange: Codable, Sendable, Hashable, Identifiable {
    public var category: String       // role, company, headline, location, skills, activity
    public var description: String
    public var severity: String       // info, notable, important
    public var oldValue: String?
    public var newValue: String?
    public var detectedAt: Date
    public var id: String { category + "|" + description + "|" + PeopleDates.string(detectedAt) }
    enum CodingKeys: String, CodingKey { case category, description, severity, oldValue = "old_value", newValue = "new_value", detectedAt = "detected_at" }
    public init(category: String, description: String, severity: String = "info", oldValue: String? = nil, newValue: String? = nil, detectedAt: Date = Date()) {
        self.category = category; self.description = description; self.severity = severity; self.oldValue = oldValue; self.newValue = newValue; self.detectedAt = detectedAt
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        category = try c.decodeIfPresent(String.self, forKey: .category) ?? ""; description = try c.decodeIfPresent(String.self, forKey: .description) ?? ""
        severity = try c.decodeIfPresent(String.self, forKey: .severity) ?? "info"
        oldValue = try c.decodeIfPresent(String.self, forKey: .oldValue); newValue = try c.decodeIfPresent(String.self, forKey: .newValue)
        detectedAt = PeopleDates.date(try? c.decodeIfPresent(String.self, forKey: .detectedAt)) ?? Date()
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(category, forKey: .category); try c.encode(description, forKey: .description); try c.encode(severity, forKey: .severity)
        try c.encodeIfPresent(oldValue, forKey: .oldValue); try c.encodeIfPresent(newValue, forKey: .newValue); try c.encode(PeopleDates.string(detectedAt), forKey: .detectedAt)
    }
}

/// What the model wrote about the person: the old `coaching_insights` JSON, typed.
public struct CoachingInsights: Codable, Sendable, Hashable {
    public var strengths: [String]
    public var growthAreas: [String]
    public var conversationStarters: [String]
    public var topicTrends: [String]
    public var goalObservations: [String]
    public var overallSummary: String
    public var generatedAt: Date?
    enum CodingKeys: String, CodingKey {
        case strengths, growthAreas = "growth_areas", conversationStarters = "conversation_starters", topicTrends = "topic_trends"
        case goalObservations = "goal_observations", overallSummary = "overall_summary", generatedAt = "generated_at"
    }
    public init(strengths: [String] = [], growthAreas: [String] = [], conversationStarters: [String] = [], topicTrends: [String] = [], goalObservations: [String] = [], overallSummary: String = "", generatedAt: Date? = nil) {
        self.strengths = strengths; self.growthAreas = growthAreas; self.conversationStarters = conversationStarters; self.topicTrends = topicTrends
        self.goalObservations = goalObservations; self.overallSummary = overallSummary; self.generatedAt = generatedAt
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        func list(_ k: CodingKeys) -> [String] {
            if let a = try? c.decodeIfPresent([String].self, forKey: k) { return a }
            if let s = try? c.decodeIfPresent(String.self, forKey: k), !s.isEmpty { return [s] }
            return []
        }
        strengths = list(.strengths); growthAreas = list(.growthAreas); conversationStarters = list(.conversationStarters)
        topicTrends = list(.topicTrends); goalObservations = list(.goalObservations)
        overallSummary = (try? c.decodeIfPresent(String.self, forKey: .overallSummary)) ?? ""
        generatedAt = PeopleDates.date(try? c.decodeIfPresent(String.self, forKey: .generatedAt))
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(strengths, forKey: .strengths); try c.encode(growthAreas, forKey: .growthAreas); try c.encode(conversationStarters, forKey: .conversationStarters)
        try c.encode(topicTrends, forKey: .topicTrends); try c.encode(goalObservations, forKey: .goalObservations); try c.encode(overallSummary, forKey: .overallSummary)
        try c.encodeIfPresent(generatedAt.map(PeopleDates.string), forKey: .generatedAt)
    }
    public var isEmpty: Bool { strengths.isEmpty && growthAreas.isEmpty && conversationStarters.isEmpty && topicTrends.isEmpty && goalObservations.isEmpty && overallSummary.isEmpty }
}

/// One person in a people notebook.
public struct PersonProfile: Codable, Sendable, Hashable, Identifiable {
    public var id: String
    public var notebookId: String
    public var name: String
    public var headline: String
    public var bio: String
    public var location: String
    public var email: String
    /// platform raw value → address.
    public var socialLinks: [String: String]
    public var currentRole: String
    public var currentCompany: String
    public var experience: [WorkExperience]
    public var education: [Education]
    public var skills: [String]
    public var linkedinPosts: [SocialPost]
    public var tweets: [SocialPost]
    /// pinned repos, contributions and the like, as read from the page.
    public var githubRepos: [String]
    public var githubContributions: String
    public var instagramPosts: [SocialPost]
    public var blogPosts: [SocialPost]
    public var coachingNotes: [CoachingNote]
    public var tags: [String]
    public var goals: [CoachingGoal]
    public var activityProfile: ActivityProfile
    public var coachingInsights: CoachingInsights?
    public var recentChanges: [ProfileChange]
    public var sources: [ProfileSource]
    public var lastCollected: [String: String]
    public var createdAt: Date
    public var updatedAt: Date

    enum CodingKeys: String, CodingKey {
        case id, notebookId = "notebook_id", name, headline, bio, location, email, socialLinks = "social_links", currentRole = "current_role", currentCompany = "current_company"
        case experience, education, skills, linkedinPosts = "linkedin_posts", tweets, githubActivity = "github_activity", instagramPosts = "instagram_posts", blogPosts = "blog_posts"
        case coachingNotes = "coaching_notes", tags, goals, activityProfile = "activity_profile", coachingInsights = "coaching_insights", recentChanges = "recent_changes"
        case sources, lastCollected = "last_collected", createdAt = "created_at", updatedAt = "updated_at"
    }
    private struct GitHub: Codable { var pinned_repos: [Repo]?; var contributions_text: String?
        struct Repo: Codable { var name: String?; var description: String?; var language: String? } }

    public init(id: String = UUID().uuidString.lowercased(), notebookId: String, name: String, socialLinks: [String: String] = [:], email: String = "", currentRole: String = "", currentCompany: String = "", tags: [String] = []) {
        self.id = id; self.notebookId = notebookId; self.name = name; headline = ""; bio = ""; location = ""; self.email = email; self.socialLinks = socialLinks
        self.currentRole = currentRole; self.currentCompany = currentCompany; experience = []; education = []; skills = []; linkedinPosts = []; tweets = []
        githubRepos = []; githubContributions = ""; instagramPosts = []; blogPosts = []; coachingNotes = []; self.tags = tags; goals = []
        activityProfile = ActivityProfile(); coachingInsights = nil; recentChanges = []; sources = []; lastCollected = [:]; createdAt = Date(); updatedAt = Date()
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(String.self, forKey: .id) ?? UUID().uuidString.lowercased()
        notebookId = try c.decodeIfPresent(String.self, forKey: .notebookId) ?? ""
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        headline = try c.decodeIfPresent(String.self, forKey: .headline) ?? ""; bio = try c.decodeIfPresent(String.self, forKey: .bio) ?? ""
        location = try c.decodeIfPresent(String.self, forKey: .location) ?? ""; email = try c.decodeIfPresent(String.self, forKey: .email) ?? ""
        socialLinks = try c.decodeIfPresent([String: String].self, forKey: .socialLinks) ?? [:]
        currentRole = try c.decodeIfPresent(String.self, forKey: .currentRole) ?? ""; currentCompany = try c.decodeIfPresent(String.self, forKey: .currentCompany) ?? ""
        experience = try c.decodeIfPresent([WorkExperience].self, forKey: .experience) ?? []; education = try c.decodeIfPresent([Education].self, forKey: .education) ?? []
        skills = try c.decodeIfPresent([String].self, forKey: .skills) ?? []
        linkedinPosts = try c.decodeIfPresent([SocialPost].self, forKey: .linkedinPosts) ?? []; tweets = try c.decodeIfPresent([SocialPost].self, forKey: .tweets) ?? []
        let gh = try? c.decodeIfPresent(GitHub.self, forKey: .githubActivity)
        githubRepos = (gh?.pinned_repos ?? []).map { r in [r.name ?? "", r.description ?? "", r.language.map { "[\($0)]" } ?? ""].filter { !$0.isEmpty }.joined(separator: ": ") }
        githubContributions = gh?.contributions_text ?? ""
        instagramPosts = try c.decodeIfPresent([SocialPost].self, forKey: .instagramPosts) ?? []; blogPosts = try c.decodeIfPresent([SocialPost].self, forKey: .blogPosts) ?? []
        coachingNotes = try c.decodeIfPresent([CoachingNote].self, forKey: .coachingNotes) ?? []
        tags = try c.decodeIfPresent([String].self, forKey: .tags) ?? []; goals = try c.decodeIfPresent([CoachingGoal].self, forKey: .goals) ?? []
        activityProfile = try c.decodeIfPresent(ActivityProfile.self, forKey: .activityProfile) ?? ActivityProfile()
        let insights = try? c.decodeIfPresent(CoachingInsights.self, forKey: .coachingInsights)
        coachingInsights = (insights?.isEmpty ?? true) ? nil : insights
        recentChanges = try c.decodeIfPresent([ProfileChange].self, forKey: .recentChanges) ?? []
        sources = try c.decodeIfPresent([ProfileSource].self, forKey: .sources) ?? []
        lastCollected = try c.decodeIfPresent([String: String].self, forKey: .lastCollected) ?? [:]
        createdAt = PeopleDates.date(try? c.decodeIfPresent(String.self, forKey: .createdAt)) ?? Date()
        updatedAt = PeopleDates.date(try? c.decodeIfPresent(String.self, forKey: .updatedAt)) ?? Date()
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id); try c.encode(notebookId, forKey: .notebookId); try c.encode(name, forKey: .name)
        try c.encode(headline, forKey: .headline); try c.encode(bio, forKey: .bio); try c.encode(location, forKey: .location); try c.encode(email, forKey: .email)
        try c.encode(socialLinks, forKey: .socialLinks); try c.encode(currentRole, forKey: .currentRole); try c.encode(currentCompany, forKey: .currentCompany)
        try c.encode(experience, forKey: .experience); try c.encode(education, forKey: .education); try c.encode(skills, forKey: .skills)
        try c.encode(linkedinPosts, forKey: .linkedinPosts); try c.encode(tweets, forKey: .tweets)
        let gh = GitHub(pinned_repos: githubRepos.map { line in
            let parts = line.components(separatedBy: ": ")
            return GitHub.Repo(name: parts.first, description: parts.count > 1 ? parts.dropFirst().joined(separator: ": ") : nil, language: nil)
        }, contributions_text: githubContributions)
        try c.encode(gh, forKey: .githubActivity)
        try c.encode(instagramPosts, forKey: .instagramPosts); try c.encode(blogPosts, forKey: .blogPosts)
        try c.encode(coachingNotes, forKey: .coachingNotes); try c.encode(tags, forKey: .tags); try c.encode(goals, forKey: .goals)
        try c.encode(activityProfile, forKey: .activityProfile); try c.encodeIfPresent(coachingInsights, forKey: .coachingInsights)
        try c.encode(recentChanges, forKey: .recentChanges); try c.encode(sources, forKey: .sources); try c.encode(lastCollected, forKey: .lastCollected)
        try c.encode(PeopleDates.string(createdAt), forKey: .createdAt); try c.encode(PeopleDates.string(updatedAt), forKey: .updatedAt)
    }

    /// The links in platform order, skipping empty ones.
    public var links: [(platform: SocialPlatform, url: String)] {
        SocialPlatform.allCases.compactMap { p in socialLinks[p.rawValue].flatMap { $0.isEmpty ? nil : (p, $0) } }
    }
    public var roleLine: String {
        var s = currentRole
        if !currentCompany.isEmpty { s += (s.isEmpty ? "" : " at ") + currentCompany }
        return s
    }
    /// The source id this person's profile is indexed under in the notebook, and the one for the notes.
    public var profileSourceId: String { "people-profile-" + id }
    public var notesSourceId: String { "people-notes-" + id }
}

/// A people notebook's configuration: the old `PeopleNotebookConfig`.
public struct PeopleConfig: Codable, Sendable, Hashable {
    public enum Schedule: String, Codable, Sendable, CaseIterable {
        case daily, weekly, manual
        public var label: String { switch self { case .daily: "Daily"; case .weekly: "Weekly"; case .manual: "Only when I ask" } }
        public var seconds: TimeInterval? { switch self { case .daily: 86_400; case .weekly: 7 * 86_400; case .manual: nil } }
    }
    public var notebookId: String
    public var notebookName: String
    public var teamName: String
    public var coachingEnabled: Bool
    public var members: [PersonProfile]
    public var collectionSchedule: Schedule
    public var lastCollectionRun: Date?
    public var createdAt: Date
    public var updatedAt: Date
    enum CodingKeys: String, CodingKey {
        case notebookId = "notebook_id", notebookName = "notebook_name", teamName = "team_name", coachingEnabled = "coaching_enabled", members
        case collectionSchedule = "collection_schedule", lastCollectionRun = "last_collection_run", createdAt = "created_at", updatedAt = "updated_at"
    }
    public init(notebookId: String = "", notebookName: String = "", teamName: String = "", coachingEnabled: Bool = false, members: [PersonProfile] = [], collectionSchedule: Schedule = .weekly) {
        self.notebookId = notebookId; self.notebookName = notebookName; self.teamName = teamName; self.coachingEnabled = coachingEnabled; self.members = members
        self.collectionSchedule = collectionSchedule; createdAt = Date(); updatedAt = Date()
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        notebookId = try c.decodeIfPresent(String.self, forKey: .notebookId) ?? ""; notebookName = try c.decodeIfPresent(String.self, forKey: .notebookName) ?? ""
        teamName = try c.decodeIfPresent(String.self, forKey: .teamName) ?? ""; coachingEnabled = try c.decodeIfPresent(Bool.self, forKey: .coachingEnabled) ?? false
        members = try c.decodeIfPresent([PersonProfile].self, forKey: .members) ?? []
        collectionSchedule = Schedule(rawValue: try c.decodeIfPresent(String.self, forKey: .collectionSchedule) ?? "") ?? .weekly
        lastCollectionRun = PeopleDates.date(try? c.decodeIfPresent(String.self, forKey: .lastCollectionRun))
        createdAt = PeopleDates.date(try? c.decodeIfPresent(String.self, forKey: .createdAt)) ?? Date()
        updatedAt = PeopleDates.date(try? c.decodeIfPresent(String.self, forKey: .updatedAt)) ?? Date()
        // The old app switched coaching on by itself once anyone had notes or goals.
        if !coachingEnabled, members.contains(where: { !$0.coachingNotes.isEmpty || !$0.goals.isEmpty }) { coachingEnabled = true }
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(notebookId, forKey: .notebookId); try c.encode(notebookName, forKey: .notebookName); try c.encode(teamName, forKey: .teamName)
        try c.encode(coachingEnabled, forKey: .coachingEnabled); try c.encode(members, forKey: .members); try c.encode(collectionSchedule, forKey: .collectionSchedule)
        try c.encodeIfPresent(lastCollectionRun.map(PeopleDates.string), forKey: .lastCollectionRun)
        try c.encode(PeopleDates.string(createdAt), forKey: .createdAt); try c.encode(PeopleDates.string(updatedAt), forKey: .updatedAt)
    }

    public var isEmpty: Bool { members.isEmpty && teamName.isEmpty }
    public func member(_ id: String) -> PersonProfile? { members.first { $0.id == id } }
    public mutating func update(_ person: PersonProfile) {
        var p = person; p.updatedAt = Date()
        if let i = members.firstIndex(where: { $0.id == p.id }) { members[i] = p } else { members.append(p) }
        updatedAt = Date()
    }
    public func isDue(now: Date = Date()) -> Bool {
        guard !members.isEmpty, let every = collectionSchedule.seconds else { return false }
        guard let last = lastCollectionRun else { return true }
        return now.timeIntervalSince(last) >= every
    }
}

/// ISO dates in the old app's shape (`datetime.utcnow().isoformat()`, no zone), read with or without a zone.
public enum PeopleDates {
    nonisolated(unsafe) private static let iso: ISO8601DateFormatter = { let f = ISO8601DateFormatter(); f.formatOptions = [.withInternetDateTime]; return f }()
    nonisolated(unsafe) private static let isoFraction: ISO8601DateFormatter = { let f = ISO8601DateFormatter(); f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]; return f }()
    public static func string(_ d: Date) -> String { iso.string(from: d) }
    public static func date(_ s: String??) -> Date? {
        guard var s = s ?? nil, !s.isEmpty else { return nil }
        // Python writes six fractional digits; the formatter reads three.
        s = s.replacingOccurrences(of: #"(\.\d{3})\d+"#, with: "$1", options: .regularExpression)
        if let d = iso.date(from: s) ?? isoFraction.date(from: s) { return d }
        // No zone: the old app wrote UTC without saying so.
        let z = s + "Z"
        if let d = iso.date(from: z) ?? isoFraction.date(from: z) { return d }
        // A bare day.
        if s.count == 10, let d = iso.date(from: s + "T00:00:00Z") { return d }
        return nil
    }
}

/// JSON files under `People/`, one per notebook.
public enum PeopleStore {
    public static var folder: URL {
        let dir = AppFolders.base.appendingPathComponent("People", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }
    static func url(notebook: String) -> URL { folder.appendingPathComponent(CanvasStore.safe(notebook) + ".json") }
    static var encoder: JSONEncoder { let e = JSONEncoder(); e.outputFormatting = [.sortedKeys, .prettyPrinted]; return e }
    public static func load(notebook: String) -> PeopleConfig {
        guard let data = try? Data(contentsOf: url(notebook: notebook)), let c = try? JSONDecoder().decode(PeopleConfig.self, from: data) else { return PeopleConfig(notebookId: notebook) }
        return c
    }
    public static func save(_ config: PeopleConfig, notebook: String) { if let data = try? encoder.encode(config) { try? data.write(to: url(notebook: notebook), options: .atomic) } }
    public static func delete(notebook: String) { try? FileManager.default.removeItem(at: url(notebook: notebook)) }
    public static func notebooks() -> [String] {
        ((try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)) ?? []).filter { $0.pathExtension == "json" }.map { $0.deletingPathExtension().lastPathComponent }
    }
    /// Reads an old `people_config.yaml`'s JSON twin, or a `people_config` document, when the import brings one.
    public static func importOld(_ data: Data, notebook: String) -> PeopleConfig? {
        guard var c = try? JSONDecoder().decode(PeopleConfig.self, from: data) else { return nil }
        c.notebookId = notebook
        for i in c.members.indices { c.members[i].notebookId = notebook }
        return c
    }
}

// MARK: - Activity

/// The old `activity_analyzer`: how often someone posts where, what kind of thing, and what lately.
public enum ActivityAnalysis {
    public static let recentPerPlatform = 3
    /// Average days between posts → label; past the last one is inactive.
    static let thresholds: [(String, Double)] = [("daily", 1.5), ("several_per_week", 4), ("weekly", 10), ("biweekly", 21), ("monthly", 45), ("rarely", 180)]
    static let rank = ["daily": 6, "several_per_week": 5, "weekly": 4, "biweekly": 3, "monthly": 2, "rarely": 1, "inactive": 0]

    public static func label(_ frequency: String) -> String {
        switch frequency {
        case "daily": "daily"; case "several_per_week": "several times a week"; case "weekly": "weekly"; case "biweekly": "every couple of weeks"
        case "monthly": "monthly"; case "rarely": "rarely"; case "inactive": "quiet"; default: frequency
        }
    }

    public static func classifyFrequency(_ posts: [SocialPost], now: Date = Date()) -> String {
        let dates = posts.compactMap { PeopleDates.date($0.date) }.sorted(by: >)
        if dates.count < 2 {
            guard let only = dates.first else { return "inactive" }
            let age = now.timeIntervalSince(only) / 86_400
            if age > 180 { return "inactive" }
            if age > 45 { return "rarely" }
            return "monthly"
        }
        var gaps: [Double] = []
        for i in 0..<(dates.count - 1) { gaps.append(max(0, dates[i].timeIntervalSince(dates[i + 1]) / 86_400)) }
        let avg = gaps.reduce(0, +) / Double(gaps.count)
        for (label, limit) in thresholds where avg <= limit { return label }
        return "inactive"
    }

    public static func contentType(_ post: SocialPost, platform: String) -> String {
        let text = post.text.lowercased()
        if platform == "github" {
            if text.contains("merge") || text.contains("pull request") { return "pull_request" }
            if text.contains("release") { return "release" }
            if text.contains("issue") { return "issue" }
            return "commit"
        }
        if !post.url.isEmpty, post.url.contains("article") || post.url.contains("/pulse/") { return "article" }
        if post.shares > 0, text.count < 100 { return "share" }
        if text.hasPrefix("re:") || text.hasPrefix("@") { return "comment" }
        if text.count > 500 { return "article" }
        return "post"
    }

    public static func overall(_ perPlatform: [String: String]) -> String {
        perPlatform.values.max { (rank[$0] ?? 0) < (rank[$1] ?? 0) } ?? "inactive"
    }

    static func posts(of person: PersonProfile, platform: SocialPlatform) -> [SocialPost] {
        switch platform { case .linkedin: person.linkedinPosts; case .twitter: person.tweets; case .instagram: person.instagramPosts; case .personalSite: person.blogPosts; case .github: [] }
    }

    public static func analyze(_ person: PersonProfile, now: Date = Date()) -> ActivityProfile {
        var out = ActivityProfile()
        out.focusSummary = person.activityProfile.focusSummary
        for (platform, _) in person.links {
            if platform == .github {
                let n = contributionsCount(person.githubContributions)
                out.platformFrequency["github"] = n > 300 ? "daily" : n > 50 ? "weekly" : n > 10 ? "monthly" : (person.githubRepos.isEmpty ? "inactive" : "rarely")
                if !person.githubRepos.isEmpty {
                    out.contentTypes["github"] = ["repository"]
                    out.recentItems += person.githubRepos.prefix(recentPerPlatform).map { RecentActivity(platform: "github", title: $0, contentType: "repository") }
                }
                continue
            }
            let posts = posts(of: person, platform: platform)
            out.platformFrequency[platform.rawValue] = classifyFrequency(posts, now: now)
            let types = Array(Set(posts.map { contentType($0, platform: platform.rawValue) })).sorted()
            if !types.isEmpty { out.contentTypes[platform.rawValue] = types }
            let sorted = posts.sorted { ($0.date, $0.text) > ($1.date, $1.text) }
            if let last = sorted.first?.date, !last.isEmpty { out.platformLastActive[platform.rawValue] = last }
            out.recentItems += sorted.prefix(recentPerPlatform).map {
                RecentActivity(platform: platform.rawValue, title: String($0.text.prefix(120)), summary: String($0.text.prefix(300)), url: $0.url, date: $0.date, contentType: contentType($0, platform: platform.rawValue))
            }
        }
        out.overallFrequency = overall(out.platformFrequency)
        out.overallLastActive = out.platformLastActive.values.max() ?? ""
        out.topics = topics(out.recentItems)
        out.lastAnalyzed = now
        return out
    }

    /// "1,234 contributions in the last year" → 1234.
    public static func contributionsCount(_ text: String) -> Int {
        let digits = text.split(whereSeparator: { !$0.isNumber && $0 != "," }).first.map { $0.replacingOccurrences(of: ",", with: "") } ?? ""
        return Int(digits) ?? 0
    }

    static let stop: Set<String> = ["the", "a", "an", "is", "are", "was", "were", "be", "been", "being", "have", "has", "had", "do", "does", "did", "will", "would", "could", "should", "may", "might", "shall", "can", "to", "of", "in", "for", "on", "with", "at", "by", "from", "as", "into", "through", "during", "before", "after", "above", "below", "between", "out", "off", "over", "under", "again", "further", "then", "once", "here", "there", "when", "where", "why", "how", "all", "each", "every", "both", "few", "more", "most", "other", "some", "such", "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very", "just", "about", "up", "it", "its", "i", "me", "my", "we", "our", "you", "your", "he", "him", "his", "she", "her", "they", "them", "their", "this", "that", "and", "but", "or", "if", "while", "what", "which", "who", "whom", "new", "also", "like", "get", "got", "one", "two", "us", "re"]

    public static func topics(_ items: [RecentActivity], max: Int = 5) -> [String] {
        var counts: [String: Int] = [:]
        for item in items {
            for raw in (item.title + " " + item.summary).lowercased().split(whereSeparator: { $0.isWhitespace }) {
                let w = raw.trimmingCharacters(in: CharacterSet(charactersIn: ".,!?;:()[]{}\"'#@-_/\\|<>"))
                if w.count > 2, !stop.contains(w), !w.hasPrefix("http") { counts[w, default: 0] += 1 }
            }
        }
        return counts.sorted { $0.value > $1.value || ($0.value == $1.value && $0.key < $1.key) }.prefix(max).map(\.key)
    }
}

// MARK: - Changes

/// The old `change_detector`: what moved between the last read and this one.
public enum ProfileChanges {
    public struct Snapshot: Codable, Sendable, Hashable {
        public var name: String, headline: String, currentRole: String, currentCompany: String, location: String
        public var skills: [String]
        public var linkedinCount: Int, tweetCount: Int
        public init(_ p: PersonProfile) {
            name = p.name; headline = p.headline; currentRole = p.currentRole; currentCompany = p.currentCompany; location = p.location
            skills = p.skills; linkedinCount = p.linkedinPosts.count; tweetCount = p.tweets.count
        }
    }

    public static func detect(previous: Snapshot?, current: PersonProfile, now: Date = Date()) -> [ProfileChange] {
        guard let prev = previous else { return [] }
        var out: [ProfileChange] = []
        func differ(_ a: String, _ b: String) -> Bool { !a.isEmpty && !b.isEmpty && a.lowercased() != b.lowercased() }
        if differ(prev.currentRole, current.currentRole) {
            out.append(.init(category: "role", description: "Role changed from “\(prev.currentRole)” to “\(current.currentRole)”", severity: "important", oldValue: prev.currentRole, newValue: current.currentRole, detectedAt: now))
        }
        if differ(prev.currentCompany, current.currentCompany) {
            out.append(.init(category: "company", description: "Moved from \(prev.currentCompany) to \(current.currentCompany)", severity: "important", oldValue: prev.currentCompany, newValue: current.currentCompany, detectedAt: now))
        }
        if !prev.headline.isEmpty, !current.headline.isEmpty, prev.headline != current.headline {
            out.append(.init(category: "headline", description: "Updated headline: “\(current.headline)”", severity: "notable", oldValue: prev.headline, newValue: current.headline, detectedAt: now))
        }
        if differ(prev.location, current.location) {
            out.append(.init(category: "location", description: "Location changed from “\(prev.location)” to “\(current.location)”", severity: "notable", oldValue: prev.location, newValue: current.location, detectedAt: now))
        }
        let old = Set(prev.skills.map { $0.lowercased() }), new = Set(current.skills.map { $0.lowercased() })
        let added = new.subtracting(old)
        if !added.isEmpty, !old.isEmpty {
            let list = added.sorted().prefix(5).joined(separator: ", ")
            out.append(.init(category: "skills", description: "New skills: \(list)", severity: "info", newValue: list, detectedAt: now))
        }
        if current.linkedinPosts.count > prev.linkedinCount, prev.linkedinCount > 0 {
            let d = current.linkedinPosts.count - prev.linkedinCount
            out.append(.init(category: "activity", description: "\(d) new LinkedIn post\(d == 1 ? "" : "s") since the last read", detectedAt: now))
        }
        if current.tweets.count > prev.tweetCount, prev.tweetCount > 0 {
            let d = current.tweets.count - prev.tweetCount
            out.append(.init(category: "activity", description: "\(d) new post\(d == 1 ? "" : "s") on X since the last read", detectedAt: now))
        }
        return out
    }
}

// MARK: - Text for the notebook

/// The profile and the notes as documents the notebook indexes (the old `profile_indexer`), so Ask can answer about
/// a person from what was read and what the user wrote.
public enum ProfileText {
    public static func profile(_ p: PersonProfile) -> String {
        var sections: [String] = []
        var header = "# Profile: \(p.name)\n"
        if !p.headline.isEmpty { header += "Headline: \(p.headline)\n" }
        if !p.roleLine.isEmpty { header += "Current Role: \(p.roleLine)\n" }
        if !p.location.isEmpty { header += "Location: \(p.location)\n" }
        if !p.tags.isEmpty { header += "Tags: \(p.tags.joined(separator: ", "))\n" }
        sections.append(header.trimmingCharacters(in: .whitespacesAndNewlines))
        if !p.bio.isEmpty { sections.append("## About\n\(p.bio)") }
        if !p.experience.isEmpty {
            sections.append("## Work Experience\n" + p.experience.prefix(10).map { e in
                var line = "- \(e.title)"
                if !e.company.isEmpty { line += " at \(e.company)" }
                if !e.dates.isEmpty { line += " (\(e.dates))" }
                if !e.description.isEmpty { line += "\n  \(e.description)" }
                return line
            }.joined(separator: "\n"))
        }
        if !p.education.isEmpty {
            sections.append("## Education\n" + p.education.map { e in
                var line = "- \(e.degree)"
                if !e.field.isEmpty { line += " in \(e.field)" }
                if !e.school.isEmpty { line += ", \(e.school)" }
                if !e.dates.isEmpty { line += " (\(e.dates))" }
                return line
            }.joined(separator: "\n"))
        }
        if !p.skills.isEmpty { sections.append("## Skills\n\(p.skills.joined(separator: ", "))") }
        func posts(_ title: String, _ list: [SocialPost], limit: Int) {
            guard !list.isEmpty else { return }
            sections.append("## \(title)\n" + list.prefix(10).map { "- \(String($0.text.prefix(limit)))" + ($0.date.isEmpty ? "" : " (\($0.date.prefix(10)))") }.joined(separator: "\n"))
        }
        posts("Recent LinkedIn Activity", p.linkedinPosts, limit: 300)
        posts("Recent Posts on X", p.tweets, limit: 280)
        if !p.githubRepos.isEmpty || !p.githubContributions.isEmpty {
            var gh = ["## GitHub Activity"] + p.githubRepos.prefix(6).map { "- \($0)" }
            if !p.githubContributions.isEmpty { gh.append("Contributions: \(p.githubContributions)") }
            sections.append(gh.joined(separator: "\n"))
        }
        posts("Blog Posts", p.blogPosts, limit: 300)
        if !p.activityProfile.focusSummary.isEmpty { sections.append("## Lately\n\(p.activityProfile.focusSummary)") }
        if let ci = p.coachingInsights, !ci.isEmpty {
            var lines = ["## Coaching Insights"]
            if !ci.overallSummary.isEmpty { lines.append(ci.overallSummary) }
            if !ci.strengths.isEmpty { lines.append("Strengths: " + ci.strengths.joined(separator: "; ")) }
            if !ci.growthAreas.isEmpty { lines.append("Growth areas: " + ci.growthAreas.joined(separator: "; ")) }
            if !ci.topicTrends.isEmpty { lines.append("Topics: " + ci.topicTrends.joined(separator: "; ")) }
            sections.append(lines.joined(separator: "\n"))
        }
        if !p.goals.isEmpty { sections.append("## Goals\n" + p.goals.map { "- \($0.goal) [\($0.status.rawValue)]" }.joined(separator: "\n")) }
        return sections.joined(separator: "\n\n")
    }

    public static func notes(_ p: PersonProfile) -> String {
        guard !p.coachingNotes.isEmpty else { return "" }
        let sorted = p.coachingNotes.sorted { $0.createdAt < $1.createdAt }
        return (["# Coaching Notes: \(p.name)\n"] + sorted.map { "## [\($0.category.label)] — \(PeopleDates.string($0.createdAt).prefix(10))\n\($0.text)" }).joined(separator: "\n\n")
    }
}

// MARK: - Prompts

/// The old prompts, word for word where they mattered: reading a profile page into fields, the activity focus line,
/// and the coaching insights with their grade-calibration rules.
public enum PeoplePrompts {
    public static let extractInstructions = "You extract structured profile data. Respond with JSON only."

    public static func extractPrompt(pageText: String, platform: SocialPlatform, url: String) -> String {
        """
        Extract structured profile information from this \(platform.label) profile page text.
        Return JSON only with these fields (use null for missing, and never invent a value the text does not give):
        {
            "name": "full name",
            "headline": "professional headline or title",
            "bio": "biography or about text",
            "location": "city/region",
            "current_role": "current job title",
            "current_company": "current employer",
            "skills": ["skill1", "skill2"],
            "experience": [{"title": "job title", "company": "employer", "dates": "Jan 2020 - Present", "is_current": true}],
            "education": [{"school": "school", "degree": "degree", "field": "field of study", "dates": "2010 - 2014"}],
            "recent_posts": [{"text": "the post's text, first 300 characters", "date": "ISO date or empty"}],
            "repositories": ["name: description [language]"],
            "contributions": "the contributions line, if there is one",
            "recent_topics": ["topic they post about"]
        }

        Page address: \(url)
        Profile text:
        \(String(pageText.prefix(6_000)))
        """
    }

    /// What the model read from a page.
    public struct Extracted: Codable, Sendable {
        public var name: String?, headline: String?, bio: String?, location: String?, current_role: String?, current_company: String?
        public var skills: [String]?
        public var experience: [WorkExperience]?
        public var education: [Education]?
        public var recent_posts: [Post]?
        public var repositories: [String]?
        public var contributions: String?
        public var recent_topics: [String]?
        public struct Post: Codable, Sendable { public var text: String?; public var date: String?; public var url: String? }
        public init() {}
        /// How many fields came back with something in them.
        public var filled: [String] {
            var f: [String] = []
            if !(name ?? "").isEmpty { f.append("name") }; if !(headline ?? "").isEmpty { f.append("headline") }; if !(bio ?? "").isEmpty { f.append("bio") }
            if !(location ?? "").isEmpty { f.append("location") }; if !(current_role ?? "").isEmpty { f.append("current_role") }; if !(current_company ?? "").isEmpty { f.append("current_company") }
            if !(skills ?? []).isEmpty { f.append("skills") }; if !(experience ?? []).isEmpty { f.append("experience") }; if !(education ?? []).isEmpty { f.append("education") }
            if !(recent_posts ?? []).isEmpty { f.append("recent_posts") }; if !(repositories ?? []).isEmpty { f.append("repositories") }; if !(contributions ?? "").isEmpty { f.append("contributions") }
            return f
        }
    }

    public static func readExtracted(_ reply: String?) -> Extracted? {
        guard let reply, let obj = JSONObjects.first(in: reply), let data = try? JSONSerialization.data(withJSONObject: obj) else { return nil }
        if let e = try? JSONDecoder().decode(Extracted.self, from: data) { return e }
        // Lists that came back as a single string, or posts as strings: read what can be read.
        var e = Extracted()
        func s(_ k: String) -> String? { (obj[k] as? String).flatMap { $0.isEmpty ? nil : $0 } }
        func list(_ k: String) -> [String]? {
            if let a = obj[k] as? [String] { return a }
            if let a = obj[k] as? [Any] { return a.compactMap { $0 as? String ?? ($0 as? [String: Any]).flatMap { d in (d["name"] as? String) ?? (d["text"] as? String) } } }
            if let one = obj[k] as? String, !one.isEmpty { return [one] }
            return nil
        }
        e.name = s("name"); e.headline = s("headline"); e.bio = s("bio") ?? s("about"); e.location = s("location")
        e.current_role = s("current_role"); e.current_company = s("current_company") ?? s("company")
        e.skills = list("skills"); e.repositories = list("repositories"); e.contributions = s("contributions"); e.recent_topics = list("recent_topics")
        e.recent_posts = list("recent_posts")?.map { Extracted.Post(text: $0, date: nil, url: nil) }
        return e
    }

    /// Folds what a page said into the person, first non-empty wins for identity, lists merge (the old `_merge_platform_data`).
    public static func merge(_ e: Extracted, from platform: SocialPlatform, into p: inout PersonProfile) {
        func take(_ v: String?, _ target: inout String) { if let v, !v.isEmpty, target.isEmpty { target = v } }
        if p.name.isEmpty { take(e.name, &p.name) }
        take(e.headline, &p.headline)
        if let bio = e.bio, !bio.isEmpty, bio.count > p.bio.count { p.bio = bio }
        take(e.location, &p.location); take(e.current_role, &p.currentRole); take(e.current_company, &p.currentCompany)
        for s in e.skills ?? [] where !s.isEmpty && !p.skills.contains(where: { $0.caseInsensitiveCompare(s) == .orderedSame }) { p.skills.append(s) }
        if let exp = e.experience, !exp.isEmpty, platform == .linkedin || p.experience.isEmpty { p.experience = Array(exp.prefix(10)) }
        if let edu = e.education, !edu.isEmpty, platform == .linkedin || p.education.isEmpty { p.education = edu }
        let posts = (e.recent_posts ?? []).compactMap { post -> SocialPost? in
            guard let t = post.text, !t.isEmpty else { return nil }
            return SocialPost(platform: platform.rawValue, text: t, date: post.date ?? "", url: post.url ?? "")
        }
        if !posts.isEmpty {
            switch platform {
            case .linkedin: p.linkedinPosts = posts
            case .twitter: p.tweets = posts
            case .instagram: p.instagramPosts = posts
            case .personalSite: p.blogPosts = posts
            case .github: break
            }
        }
        if platform == .github {
            if let repos = e.repositories, !repos.isEmpty { p.githubRepos = repos }
            if let c = e.contributions, !c.isEmpty { p.githubContributions = c }
        }
        if let topics = e.recent_topics, !topics.isEmpty, p.activityProfile.topics.isEmpty { p.activityProfile.topics = topics }
    }

    public static let focusInstructions = "You are a concise professional analyst. Provide brief activity insights."

    public static func focusPrompt(_ p: PersonProfile) -> String? {
        var parts: [String] = []
        let a = p.activityProfile
        if !a.platformFrequency.isEmpty { parts.append("Platform activity levels: " + a.platformFrequency.sorted { $0.key < $1.key }.map { "\($0.key): \($0.value)" }.joined(separator: ", ")) }
        if !a.topics.isEmpty { parts.append("Key topics: \(a.topics.joined(separator: ", "))") }
        if !a.recentItems.isEmpty { parts.append("Recent activity:\n" + a.recentItems.prefix(6).map { "- [\($0.platform)] \($0.title.prefix(100))" }.joined(separator: "\n")) }
        if !p.githubRepos.isEmpty { parts.append("GitHub repos: " + p.githubRepos.prefix(5).joined(separator: ", ")) }
        if !p.githubContributions.isEmpty { parts.append("GitHub: \(p.githubContributions)") }
        guard !parts.isEmpty else { return nil }
        return """
        Based on the following activity data for \(p.name), write a concise 2-3 sentence insight about what they've been focused on, their engagement level, and any notable patterns.

        \(parts.joined(separator: "\n"))

        Be specific and actionable. Write in third person. Do not use bullet points — just flowing sentences.
        """
    }

    public static let coachingInstructions = "You are a professional executive coach and talent analyst. Analyze the provided profile data and generate actionable coaching insights. Be specific and evidence-based — cite observable behaviors from the data. Respond with JSON only."

    /// The old `_build_profile_summary`.
    public static func profileSummary(_ p: PersonProfile) -> String {
        var parts = ["Name: \(p.name)"]
        if !p.roleLine.isEmpty { parts.append("Role: \(p.roleLine)") }
        if !p.headline.isEmpty { parts.append("Headline: \(p.headline)") }
        if !p.location.isEmpty { parts.append("Location: \(p.location)") }
        if !p.bio.isEmpty { parts.append("\nBio: \(p.bio.prefix(500))") }
        if !p.skills.isEmpty { parts.append("\nSkills: \(p.skills.prefix(20).joined(separator: ", "))") }
        if !p.experience.isEmpty {
            parts.append((["\nExperience:"] + p.experience.prefix(5).map { e in "- \(e.title)" + (e.company.isEmpty ? "" : " at \(e.company)") + (e.dates.isEmpty ? "" : " (\(e.dates))") }).joined(separator: "\n"))
        }
        func posts(_ title: String, _ list: [SocialPost]) {
            guard !list.isEmpty else { return }
            let sorted = list.sorted { $0.date < $1.date }.suffix(5)
            parts.append((["\n\(title) (chronological):"] + sorted.map { ($0.date.isEmpty ? "" : "[\($0.date.prefix(10))] ") + "- " + String($0.text.prefix(200)) }).joined(separator: "\n"))
        }
        posts("Recent LinkedIn Posts", p.linkedinPosts)
        posts("Recent Posts on X", p.tweets)
        if !p.githubRepos.isEmpty { parts.append((["\nGitHub Pinned Repos:"] + p.githubRepos.prefix(4).map { "- \($0)" }).joined(separator: "\n")) }
        if !p.tags.isEmpty { parts.append("\nTags: \(p.tags.joined(separator: ", "))") }
        return parts.joined(separator: "\n")
    }

    /// Context the notebook adds: passages, topics, findings, the questions asked lately.
    public struct Context: Sendable {
        public var passages: [(date: String, source: String, text: String)] = []
        public var topics: [(name: String, count: Int, keywords: [String])] = []
        public var findings: [(kind: String, title: String)] = []
        public var questions: [String] = []
        public var userCoaching: String = ""
        public init() {}
    }

    /// The old `_build_prompt`, with the manager's notes as primary context and the grade-calibration rules.
    public static func coachingPrompt(_ p: PersonProfile, context: Context) -> String {
        var goals = ""
        if !p.goals.isEmpty { goals = "\n\nCurrent Goals:\n" + p.goals.map { "- \($0.goal) [\($0.status.rawValue)]" }.joined(separator: "\n") }
        var notes = ""
        if !p.coachingNotes.isEmpty {
            notes = "\n\nCoaching Notes (chronological):\n" + p.coachingNotes.sorted { $0.createdAt < $1.createdAt }
                .map { "- [\($0.category.label)] (\(PeopleDates.string($0.createdAt).prefix(10))) \($0.text)" }.joined(separator: "\n")
        }
        var notebook = ""
        if !context.passages.isEmpty {
            notebook = "\n\nNotebook Documents (chronological — team priorities, performance frameworks, uploaded materials, discovered items):\n"
                + context.passages.prefix(6).map { "[\($0.date.isEmpty ? "unknown date" : $0.date) — \($0.source.isEmpty ? "source" : $0.source)] \($0.text.prefix(300))" }.joined(separator: "\n---\n")
        }
        var topics = ""
        if !context.topics.isEmpty { topics = "\n\nTopic clusters in notebook:\n" + context.topics.prefix(8).map { "- \($0.name) (\($0.count) passages): \($0.keywords.prefix(5).joined(separator: ", "))" }.joined(separator: "\n") }
        var findings = ""
        if !context.findings.isEmpty { findings = "\n\nManager's bookmarked findings:\n" + context.findings.prefix(8).map { "- [\($0.kind)] \($0.title)" }.joined(separator: "\n") }
        var questions = ""
        if !context.questions.isEmpty { questions = "\n\nRecent research questions from the manager:\n" + context.questions.prefix(10).map { "- \($0)" }.joined(separator: "\n") }
        return """
        Analyze this person's profile and generate coaching insights.

        IMPORTANT: The manager's coaching notes and goals below are PRIMARY context.
        These are direct observations from the person's manager and should be treated
        as the most authoritative data source. If a coaching note states a grade or
        level (e.g. "grade 12 SE"), that is the person's CURRENT level — anchor ALL
        evaluation there. Do not compare them to lower grades. Everything below their
        stated level is assumed mastery. Only surface insights that are relevant to
        their actual grade and the evidence in the notebook.

        \(profileSummary(p))\(goals)\(notes)\(notebook)\(topics)\(findings)\(questions)

        Generate structured coaching insights as JSON:
        {
            "strengths": ["Specific strength observed — cite evidence from activity AND/OR coaching notes"],
            "growth_areas": ["Specific area for development — factor in role expectations from coaching notes"],
            "conversation_starters": ["Specific 1:1 topic grounded in recent activity AND coaching context"],
            "topic_trends": ["Topic or theme they've been engaging with recently"],
            "goal_observations": ["Observation about progress toward stated goals (if any)"],
            "overall_summary": "2-3 sentence coaching summary that integrates coaching notes context"
        }

        Rules:
        - Be specific, not generic. Reference actual data points.
        - Each item should be 1-2 sentences max.
        - If data is limited for a category, return fewer items or an empty list.
        - Strengths and growth_areas: 2-4 items each.
        - Conversation starters: 2-3 relevant topics for a coaching conversation.
        - Topic trends: top 2-3 themes from their recent activity.
        - Goal observations: only if goals are set, otherwise empty list.

        CRITICAL — Grade/Level Calibration:
        - Coaching notes specify the person's current grade or level (e.g. "grade 12 SE").
          This is the ONLY level that matters for evaluation.
        - A person at grade N is ASSUMED to already meet ALL expectations for grades
          below N. Do NOT praise them for meeting lower-grade expectations — that is
          table stakes, not a strength. Do NOT suggest they "move beyond" a lower
          grade — they already have.
        - Strengths should highlight where the person meets or EXCEEDS their current
          grade expectations, or where they show distinctive capability relative to
          peers at the same level.
        - Growth areas should ONLY be surfaced when notebook evidence (uploaded docs,
          collected activity, coaching notes) shows a specific pattern of NOT meeting
          their current-grade expectations. If there is no evidence of a gap, do NOT
          invent one. Prefer an empty list over speculative growth areas.
        - When referencing framework documents (e.g. expectations docs), ONLY
          reference the expectations at the person's stated grade level or above.
          Lower-grade expectations are irrelevant context.

        JSON:
        """
    }

    public static func readInsights(_ reply: String?, now: Date = Date()) -> CoachingInsights? {
        guard let reply, let obj = JSONObjects.first(in: reply), let data = try? JSONSerialization.data(withJSONObject: obj),
              var ci = try? JSONDecoder().decode(CoachingInsights.self, from: data), !ci.isEmpty else { return nil }
        ci.generatedAt = now
        return ci
    }

    /// The three questions the old generator asked the notebook for a person.
    public static func contextQueries(for name: String) -> [String] {
        ["team priorities goals objectives for \(name)", "performance expectations development framework grade level accountability", "coaching notes observations feedback for \(name)"]
    }

    /// Enough profile to be worth a coaching read (the old 50-character floor).
    public static func hasEnough(_ p: PersonProfile) -> Bool { profileSummary(p).count >= 50 && (!p.bio.isEmpty || !p.experience.isEmpty || !p.skills.isEmpty || !p.coachingNotes.isEmpty || !p.linkedinPosts.isEmpty || !p.tweets.isEmpty || !p.githubRepos.isEmpty || !p.blogPosts.isEmpty) }
}
