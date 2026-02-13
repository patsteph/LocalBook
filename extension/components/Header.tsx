import { useState } from "react"
import type { Notebook } from "../types"
import { ImportMenu } from "./ImportMenu"

interface HeaderProps {
  notebooks: Notebook[]
  selectedNotebook: string
  primaryNotebookId: string | null
  onSelectNotebook: (id: string) => void
  onCreateNotebook: (name: string) => Promise<void>
  onMessage: (msg: string, type: "success" | "error" | "info") => void
  onRefresh: () => void
  loading: boolean
}

export function Header({
  notebooks,
  selectedNotebook,
  primaryNotebookId,
  onSelectNotebook,
  onCreateNotebook,
  onMessage,
  onRefresh,
  loading
}: HeaderProps) {
  const [notebookExpanded, setNotebookExpanded] = useState(false)
  const [creatingNotebook, setCreatingNotebook] = useState(false)
  const [newNotebookName, setNewNotebookName] = useState("")

  const handleCreate = async () => {
    if (!newNotebookName.trim()) return
    await onCreateNotebook(newNotebookName.trim())
    setNewNotebookName("")
    setCreatingNotebook(false)
  }

  return (
    <div className="px-3 py-2 border-b border-gray-700">
      <div className="flex items-center justify-between gap-3">
        {/* Left: Logo + Notebook */}
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <span className="text-lg shrink-0">📚</span>
          <span className="font-bold text-sm shrink-0">LocalBook</span>
        
          {/* Notebook Selector - inline */}
          <div className="relative min-w-0 flex-1">
          <button
            onClick={() => setNotebookExpanded(!notebookExpanded)}
            className="flex items-center gap-1 px-2 py-1 bg-gray-800 border border-gray-600 rounded text-sm hover:bg-gray-750 max-w-full"
          >
            <span className="truncate text-xs">
              {notebooks.find(n => n.id === selectedNotebook)?.name || "Select"}
            </span>
            {selectedNotebook === primaryNotebookId && (
              <span className="text-purple-400 text-xs">★</span>
            )}
            <span className="text-gray-400 text-xs">{notebookExpanded ? "▲" : "▼"}</span>
          </button>
          
          {/* Dropdown */}
          {notebookExpanded && (
            <div className="absolute z-50 left-0 right-0 mt-1 bg-gray-800 border border-gray-600 rounded shadow-lg max-h-60 overflow-auto" style={{minWidth: "200px"}}>
              {notebooks.map((nb) => (
                <button
                  key={nb.id}
                  onClick={() => {
                    onSelectNotebook(nb.id)
                    setNotebookExpanded(false)
                  }}
                  className={`w-full text-left px-3 py-2 text-sm hover:bg-gray-700 flex items-center justify-between ${
                    nb.id === selectedNotebook ? "bg-gray-700" : ""
                  }`}
                >
                  <span className="truncate">{nb.name}</span>
                  <span className="text-xs text-gray-500 ml-2 flex items-center gap-1">
                    {nb.id === primaryNotebookId && <span className="text-purple-400">★</span>}
                    {nb.source_count}
                  </span>
                </button>
              ))}
              
              {/* Create new notebook */}
              {creatingNotebook ? (
                <div className="p-2 border-t border-gray-700">
                  <input
                    type="text"
                    value={newNotebookName}
                    onChange={(e) => setNewNotebookName(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleCreate()}
                    placeholder="Notebook name..."
                    className="w-full p-2 bg-gray-700 border border-gray-600 rounded text-sm"
                    autoFocus
                  />
                  <div className="flex gap-2 mt-2">
                    <button
                      onClick={handleCreate}
                      disabled={!newNotebookName.trim() || loading}
                      className="flex-1 px-2 py-1 bg-purple-600 hover:bg-purple-700 disabled:bg-gray-700 rounded text-xs"
                    >
                      Create
                    </button>
                    <button
                      onClick={() => { setCreatingNotebook(false); setNewNotebookName("") }}
                      className="px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded text-xs"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  onClick={() => setCreatingNotebook(true)}
                  className="w-full text-left px-3 py-2 text-sm text-purple-400 hover:bg-gray-700 border-t border-gray-700"
                >
                  + New Notebook
                </button>
              )}
            </div>
          )}
          </div>
        </div>
        
        {/* Right: Status + Import */}
        <div className="flex items-center gap-2 shrink-0">
          <div title="Connected to LocalBook">
            <div className="w-2.5 h-2.5 rounded-full bg-green-500"></div>
          </div>
          {selectedNotebook && (
            <ImportMenu
              notebookId={selectedNotebook}
              onMessage={onMessage}
              onRefresh={onRefresh}
            />
          )}
        </div>
      </div>
    </div>
  )
}
