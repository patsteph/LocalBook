import type { PageInfo } from "../types"

interface PageInfoBarProps {
  pageInfo: PageInfo
  onCopyCleanUrl: () => void
}

export function PageInfoBar({ pageInfo, onCopyCleanUrl }: PageInfoBarProps) {
  return (
    <div className="px-3 py-2 border-b border-gray-700 flex items-center gap-2">
      <div className="flex-1 min-w-0">
        <div className="text-xs font-medium text-gray-300 truncate">
          {pageInfo.title}
        </div>
        <div className="text-xs text-gray-500 truncate">
          {pageInfo.domain}
        </div>
      </div>
      <button
        onClick={onCopyCleanUrl}
        className="text-xs text-blue-400 hover:text-blue-300 shrink-0"
        title="Copy clean URL"
      >
        📋
      </button>
    </div>
  )
}
