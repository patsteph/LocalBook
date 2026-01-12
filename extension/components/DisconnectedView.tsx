interface DisconnectedViewProps {
  onRetry: () => void
}

export function DisconnectedView({ onRetry }: DisconnectedViewProps) {
  return (
    <div className="p-4 bg-gray-900 text-white min-h-screen flex flex-col items-center justify-center">
      <div className="text-6xl mb-4">📚</div>
      <h1 className="text-xl font-bold mb-2">LocalBook</h1>
      <p className="text-gray-400 text-center mb-4">
        Cannot connect to LocalBook.
        <br />Make sure the app is running.
      </p>
      <button
        onClick={onRetry}
        className="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded font-medium"
      >
        Retry Connection
      </button>
    </div>
  )
}
