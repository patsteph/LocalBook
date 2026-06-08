import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'
// Side-effect import: registers built-in artifact renderers (markdown,
// svg, mermaid, klein, json:chart) with the registry before first render.
import './components/artifact/registerRenderers'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
