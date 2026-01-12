import type { SearchResult } from "../types"

interface ResearchViewProps {
  searchQuery: string
  selectedSite: string
  searchResults: SearchResult[]
  loading: boolean
  onQueryChange: (query: string) => void
  onSiteChange: (site: string) => void
  onSearch: () => void
  onQuickAdd: (result: SearchResult) => void
  onBack: () => void
}

export function ResearchView({
  searchQuery,
  selectedSite,
  searchResults,
  loading,
  onQueryChange,
  onSiteChange,
  onSearch,
  onQuickAdd,
  onBack
}: ResearchViewProps) {
  return (
    <div className="flex flex-col h-full">
      {/* Back button */}
      <button
        onClick={onBack}
        className="text-xs text-gray-400 hover:text-gray-200 mb-2 flex items-center gap-1"
      >
        ← Back to actions
      </button>

      {/* Search controls */}
      <div className="space-y-2 mb-3">
        <div className="flex gap-2">
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => onQueryChange(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && onSearch()}
            placeholder="Search terms..."
            className="flex-1 p-2 bg-gray-800 border border-gray-600 rounded text-sm"
          />
          <button
            onClick={onSearch}
            disabled={loading}
            className="px-3 py-2 bg-emerald-600 hover:bg-emerald-700 disabled:bg-gray-700 rounded text-sm"
          >
            🔍
          </button>
        </div>
        <select
          value={selectedSite}
          onChange={(e) => onSiteChange(e.target.value)}
          className="w-full p-2 bg-gray-800 border border-gray-600 rounded text-xs"
        >
          <option value="">All sources</option>
          <option value="youtube.com">📺 YouTube</option>
          <option value="arxiv.org">📄 ArXiv</option>
          <option value="github.com">💻 GitHub</option>
          <option value="reddit.com">🗣️ Reddit</option>
          <option value="news.ycombinator.com">🟠 Hacker News</option>
          <option value="pubmed.gov">🏥 PubMed</option>
          <option value="wikipedia.org">📚 Wikipedia</option>
        </select>
      </div>

      {/* Search results */}
      <div className="flex-1 overflow-auto space-y-2">
        {searchResults.length === 0 && !loading && (
          <div className="text-center text-gray-500 py-4">
            <p className="text-sm">No results yet. Modify search terms or select a source.</p>
          </div>
        )}
        {searchResults.map((result, i) => (
          <div key={i} className="p-2 bg-gray-800 rounded border border-gray-700">
            <div className="flex items-start justify-between gap-2">
              {/* Thumbnail for YouTube */}
              {result.thumbnail && (
                <img 
                  src={result.thumbnail} 
                  alt="" 
                  className="w-16 h-12 object-cover rounded shrink-0"
                />
              )}
              <div className="flex-1 min-w-0">
                <a
                  href={result.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm text-blue-400 hover:text-blue-300 font-medium line-clamp-2"
                >
                  {result.title}
                </a>
                {/* Metadata row: source + duration/read time + views */}
                <div className="flex flex-wrap items-center gap-1.5 mt-1">
                  <span className="text-xs bg-gray-700 px-1.5 py-0.5 rounded text-gray-400">
                    {result.source_site}
                  </span>
                  {result.metadata?.duration && (
                    <span className="text-xs bg-purple-900/50 text-purple-300 px-1.5 py-0.5 rounded">
                      ⏱️ {result.metadata.duration}
                    </span>
                  )}
                  {result.metadata?.view_count && (
                    <span className="text-xs bg-blue-900/50 text-blue-300 px-1.5 py-0.5 rounded">
                      👁️ {result.metadata.view_count}
                    </span>
                  )}
                  {result.metadata?.read_time && (
                    <span className="text-xs bg-green-900/50 text-green-300 px-1.5 py-0.5 rounded">
                      📖 {result.metadata.read_time}
                    </span>
                  )}
                  {result.published_date && (
                    <span className="text-xs text-gray-500">
                      {result.published_date}
                    </span>
                  )}
                </div>
                <p className="text-xs text-gray-400 mt-1 line-clamp-2">{result.snippet}</p>
              </div>
              <button
                onClick={() => onQuickAdd(result)}
                disabled={loading}
                className="shrink-0 px-2 py-1 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 rounded text-xs"
                title="Add to notebook"
              >
                + Add
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
