export interface Notebook {
  id: string
  name: string
  source_count: number
}

export interface PageInfo {
  url: string
  cleanUrl: string
  title: string
  domain: string
}

export interface SummaryResult {
  summary: string
  key_points: string[]
  key_concepts: string[]
  reading_time: number
  raw_content?: string
  outbound_links?: OutboundLink[]
}

export interface OutboundLink {
  url: string
  text: string
  context: string
}

export interface TransformResult {
  type: string
  content: string
  timestamp: number
}

export interface LinkInfo {
  outgoing: string[]
  incoming: string[]
}

export interface ChatMessage {
  role: "user" | "assistant"
  content: string
  timestamp: number
}

export interface PageContext {
  url: string
  title: string
  summary?: string
  content?: string
}

export type ViewMode = "actions" | "chat" | "research" | "transform"
export type ActionType = "summary" | "scrape" | "links" | "compare" | "automate" | "chat" | null

export interface SearchResult {
  title: string
  url: string
  snippet: string
  source_site: string
  published_date?: string
  author?: string
  thumbnail?: string
  metadata?: {
    duration?: string
    view_count?: string
    read_time?: string
    video_id?: string
  }
}

export interface JourneyEntry {
  url: string
  title: string
  actions: string[]
  concepts: string[]
  timestamp: number
}

export interface SessionState {
  summaryResult: SummaryResult | null
  searchResults: SearchResult[]
  chatMessages: ChatMessage[]
  pageActions: string[]
  currentAction: ActionType
  viewMode: ViewMode
  timestamp: number
}

export const API_BASE = "http://localhost:8000"
