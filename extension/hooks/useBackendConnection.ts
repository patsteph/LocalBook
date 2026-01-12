import type { Notebook } from "../types"
import { API_BASE } from "../types"

export async function checkConnection(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/browser/status`)
    return res.ok
  } catch {
    return false
  }
}

export async function fetchNotebooks(): Promise<Notebook[]> {
  try {
    const res = await fetch(`${API_BASE}/browser/notebooks`)
    if (res.ok) {
      return await res.json()
    }
    return []
  } catch (e) {
    console.error("Failed to fetch notebooks:", e)
    return []
  }
}

export async function fetchPrimaryNotebookId(): Promise<string | null> {
  try {
    const res = await fetch(`${API_BASE}/settings/primary-notebook`)
    if (res.ok) {
      const data = await res.json()
      return data.primary_notebook_id || null
    }
    return null
  } catch {
    return null
  }
}

export async function createNotebook(name: string): Promise<Notebook | null> {
  try {
    const res = await fetch(`${API_BASE}/notebooks/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: name.trim() })
    })
    if (!res.ok) throw new Error(await res.text())
    return await res.json()
  } catch (e) {
    console.error("Failed to create notebook:", e)
    return null
  }
}
