import { useEffect, useRef, useState } from 'react'
import { API_URL, newSessionId, sendChat } from './api'

const FAILURE_OPTIONS = [
  { value: '', label: 'No simulated failure' },
  { value: 'key_1=429', label: 'key_1 = 429 (rate limit)' },
  { value: 'key_1=500', label: 'key_1 = 500 (server error)' },
  { value: '*=429', label: 'all keys = 429' },
]

function Metadata({ meta }) {
  const cached = meta.resolution_source === 'LLM_CACHE'
  return (
    <div className="meta">
      <span className={`chip ${cached ? 'chip-cache' : 'chip-live'}`} data-testid="meta-source">
        {meta.resolution_source}
      </span>
      <span className="chip" title="Model">
        <span className="chip-label">model</span>
        <span data-testid="meta-model">{meta.model}</span>
      </span>
      <span className="chip" title="Server-measured latency">
        <span className="chip-label">latency</span>
        <span data-testid="meta-latency">{meta.latency_ms} ms</span>
      </span>
      <span className="chip" title="Prompt tokens for this request">
        <span className="chip-label">in</span>
        <span data-testid="meta-input-tokens">{meta.input_tokens}</span>
      </span>
      <span className="chip" title="Completion tokens for this reply">
        <span className="chip-label">out</span>
        <span data-testid="meta-output-tokens">{meta.output_tokens}</span>
      </span>
      <span className="chip" title="Key that answered / keys that failed first">
        <span className="chip-label">key</span>
        {meta.key_id ?? 'none (cache)'}
        {meta.fallback_attempts > 0 && <span className="chip-warn"> · {meta.fallback_attempts} failover</span>}
      </span>
    </div>
  )
}

function Message({ msg }) {
  if (msg.role === 'user') {
    return (
      <div className="row row-user">
        <div className="bubble bubble-user">{msg.content || <em>(empty message)</em>}</div>
      </div>
    )
  }
  if (msg.role === 'error') {
    return (
      <div className="row">
        <div className="bubble bubble-error" role="alert">
          <strong>Error{msg.status ? ` ${msg.status}` : ''}:</strong> {msg.content}
        </div>
      </div>
    )
  }
  return (
    <div className="row">
      <div className="bubble bubble-assistant" data-testid="assistant-message">
        <div className="reply">{msg.content}</div>
        <Metadata meta={msg.metadata} />
      </div>
    </div>
  )
}

export default function App() {
  // A new session id on every page load (useState initializer runs once).
  const [sessionId, setSessionId] = useState(() => newSessionId())
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [simulateFailure, setSimulateFailure] = useState('')
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef(null)

  // Scroll to the newest message whenever the list changes.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  async function handleSend() {
    if (loading) return
    const text = input
    setInput('') // the input is empty right after sending
    setMessages((prev) => [...prev, { role: 'user', content: text }])
    setLoading(true)
    try {
      const data = await sendChat({ sessionId, message: text, systemPrompt, simulateFailure })
      setMessages((prev) => [...prev, { role: 'assistant', content: data.reply, metadata: data.metadata }])
    } catch (err) {
      setMessages((prev) => [...prev, { role: 'error', content: err.message, status: err.status }])
    } finally {
      setLoading(false)
    }
  }

  function handleKeyDown(e) {
    // Enter sends, Shift+Enter adds a new line
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  function startNewChat() {
    setSessionId(newSessionId())
    setMessages([])
  }

  return (
    <div className="app">
      <header className="header">
        <div>
          <h1>Lyftr Chatbot</h1>
          <p className="sub">
            Session <code>{sessionId}</code> · backend <code>{API_URL}</code>
          </p>
        </div>
        <button className="ghost" onClick={startNewChat} disabled={loading}>
          New chat
        </button>
      </header>

      <details className="settings">
        <summary>Session settings</summary>
        <label>
          System prompt (optional)
          <textarea
            data-testid="system-prompt-input"
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            placeholder="You are a helpful, concise assistant."
            rows={2}
          />
        </label>
        <label>
          Failover demo (sends the X-Simulate-Failure header)
          <select value={simulateFailure} onChange={(e) => setSimulateFailure(e.target.value)}>
            {FAILURE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>
      </details>

      <main className="chat">
        {messages.length === 0 && !loading && (
          <div className="empty">Ask anything to start. Each reply shows its source, model, latency and tokens.</div>
        )}
        {messages.map((m, i) => (
          <Message key={i} msg={m} />
        ))}
        {loading && (
          <div className="row">
            <div className="bubble bubble-assistant loading" aria-live="polite">
              <span className="dot" /><span className="dot" /><span className="dot" /> Thinking…
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </main>

      <footer className="composer">
        <textarea
          data-testid="chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Type a message… (Enter to send, Shift+Enter for a new line)"
          rows={1}
        />
        <button data-testid="send-button" onClick={handleSend} disabled={loading}>
          {loading ? 'Sending…' : 'Send'}
        </button>
      </footer>
    </div>
  )
}
