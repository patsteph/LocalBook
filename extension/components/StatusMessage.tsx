interface StatusMessageProps {
  message: string
  type: "success" | "error" | "info"
}

export function StatusMessage({ message, type }: StatusMessageProps) {
  if (!message) return null

  return (
    <div className={`mx-3 mt-3 p-2 rounded text-sm ${
      type === "success" ? "bg-green-900/50 text-green-300" :
      type === "error" ? "bg-red-900/50 text-red-300" :
      "bg-gray-800 text-gray-300"
    }`}>
      {message}
    </div>
  )
}
