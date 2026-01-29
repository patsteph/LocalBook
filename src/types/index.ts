// Type definitions for the application

export interface Notebook {
  id: string;
  title: string;
  description?: string;
  color?: string;
  created_at: string;
  source_count: number;
}

export interface Source {
  id: string;
  filename: string;
  format: string;
  chunks?: number;
  characters?: number;
  char_count?: number;  // Web sources use this field
  word_count?: number;  // Web sources use this field
  status: string;
  type?: string;
  url?: string;
  tags?: string[];  // v0.6.0: Document tags
}

export interface Citation {
  number: number;
  source_id: string;
  filename: string;
  chunk_index: number;
  text: string;
  snippet: string;
  page?: number;
  confidence: number;
  confidence_level: 'high' | 'medium' | 'low';
}

export interface WebSource {
  title: string;
  snippet: string;
  url: string;
}

// Inline visual data for Chat Canvas
export interface InlineVisualData {
  id: string;
  type: 'svg' | 'mermaid';
  code: string;
  title?: string;
  template_id?: string;
  pattern?: string;
  tagline?: string;  // Editable summary line shown below visual
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  quickSummary?: string;  // Quick summary from fast model (shown before detailed answer)
  statusMessage?: string;  // Progressive status update (Phase 1.2)
  citations?: Citation[];
  web_sources?: WebSource[];
  follow_up_questions?: string[];
  timestamp: Date;
  lowConfidenceQuery?: string;  // The query to use for web search if low confidence
  memoryUsed?: string[];  // Types of memory used: "core_context", "retrieved_memories"
  memoryContextSummary?: string;  // Brief summary of memory context used
  inlineVisual?: InlineVisualData;  // Canvas: inline visual for this message
  alternativeVisuals?: InlineVisualData[];  // Canvas: alternative visual options
  visualLoading?: boolean;  // Canvas: visual is being generated
  visualLoadingMessage?: string;  // Canvas: custom loading message (e.g., "Analyzing your guidance...")
}

export interface ChatQuery {
  notebook_id: string;
  question: string;
  source_ids?: string[];
  top_k?: number;
  enable_web_search?: boolean;
  llm_provider?: string;
  deep_think?: boolean;  // Enable Deep Think mode with chain-of-thought reasoning
}

export interface ChatResponse {
  answer: string;
  citations: Citation[];
  sources: string[];
  web_sources?: WebSource[];
  follow_up_questions?: string[];
  low_confidence?: boolean;  // True when < 3 citations found
  memory_used?: string[];  // Types of memory used
  memory_context_summary?: string;  // Brief summary of memory context
}

export interface Skill {
  skill_id: string;
  name: string;
  description?: string;
  system_prompt: string;
  is_builtin: boolean;
}

export interface AudioGeneration {
  audio_id: string;
  notebook_id: string;
  script: string;
  audio_file_path?: string;
  duration_seconds?: number;
  status: string;
  error_message?: string;
  created_at: string;
}

export interface AudioGenerateRequest {
  notebook_id: string;
  topic?: string;
  duration_minutes: number;
  skill_id?: string;
  host1_gender: string;
  host2_gender: string;
  accent: string;
}

export interface Highlight {
  highlight_id: string;
  notebook_id: string;
  source_id: string;
  start_offset: number;
  end_offset: number;
  highlighted_text: string;
  color: string;
  annotation: string;
  created_at: string;
  updated_at: string;
}

export interface HighlightCreate {
  notebook_id: string;
  source_id: string;
  start_offset: number;
  end_offset: number;
  highlighted_text: string;
  color?: string;
  annotation?: string;
}
