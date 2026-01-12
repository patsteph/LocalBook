import type { LinkInfo } from "../types"
import { extractDomain } from "../hooks/usePageContent"

interface ScrapeResultProps {
  result: string
}

export function ScrapeResult({ result }: ScrapeResultProps) {
  return (
    <div className="p-3 bg-green-900/30 rounded">
      <pre className="text-sm text-green-300 whitespace-pre-wrap">{result}</pre>
    </div>
  )
}

interface LinksResultProps {
  linksResult: LinkInfo
}

export function LinksResult({ linksResult }: LinksResultProps) {
  return (
    <div className="space-y-3">
      <h3 className="font-bold text-sm text-gray-300">
        Outgoing Links ({linksResult.outgoing.length})
      </h3>
      <div className="space-y-1 max-h-80 overflow-auto">
        {linksResult.outgoing.map((link, i) => (
          <a
            key={i}
            href={link}
            target="_blank"
            rel="noopener noreferrer"
            className="block text-xs text-blue-400 hover:text-blue-300 truncate"
          >
            {extractDomain(link)}: {link}
          </a>
        ))}
      </div>
    </div>
  )
}

interface CompareResultProps {
  result: string
}

export function CompareResult({ result }: CompareResultProps) {
  return (
    <div className="space-y-3">
      <h3 className="font-bold text-sm text-gray-300">Notebook Comparison</h3>
      <p className="text-sm text-gray-200 whitespace-pre-wrap">
        {result}
      </p>
    </div>
  )
}
