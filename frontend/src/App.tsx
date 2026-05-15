import { useState, useEffect } from 'react'
import './App.css'

interface RepoEvent {
  id: number
  event_type: string
  external_id: string
  content: string
  created_at: string
}

interface AgentAdvice {
  id: number
  event_id: number
  advice_type: string
  title: string
  content: string
  created_at: string
}

function App() {
  const [events, setEvents] = useState<RepoEvent[]>([])
  const [advice, setAdvice] = useState<AgentAdvice[]>([])
  const [status, setStatus] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  const fetchData = async () => {
    try {
      const [statusRes, eventsRes, adviceRes] = await Promise.all([
        fetch('http://localhost:8000/status'),
        fetch('http://localhost:8000/events'),
        fetch('http://localhost:8000/advice')
      ])
      
      const statusData = await statusRes.json()
      const eventsData = await eventsRes.json()
      const adviceData = await adviceRes.json()

      setStatus(statusData)
      setEvents(eventsData)
      setAdvice(adviceData)
    } catch (error) {
      console.error("Error fetching data:", error)
    } finally {
      setLoading(false)
    }
  }

  const triggerPoll = async () => {
    setLoading(true)
    await fetch('http://localhost:8000/trigger-poll', { method: 'POST' })
    fetchData()
  }

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 30000) // Poll every 30s
    return () => clearInterval(interval)
  }, [])

  return (
    <div className="container">
      <header>
        <h1>Certmate-Agent</h1>
        {status && (
          <div className="status-badge">
            Monitoring: <span>{status.repo}</span>
          </div>
        )}
        <button onClick={triggerPoll} disabled={loading} className="poll-button">
          {loading ? 'Polling...' : 'Check Now'}
        </button>
      </header>

      <main className="dashboard">
        <section className="advice-section">
          <h2>Agent Advice</h2>
          <div className="advice-list">
            {advice.length === 0 ? (
              <p className="empty-state">No advice yet. Try checking for updates.</p>
            ) : (
              advice.map((item) => (
                <div key={item.id} className={`advice-card ${item.advice_type}`}>
                  <h3>{item.title}</h3>
                  <p>{item.content}</p>
                  <span className="timestamp">{new Date(item.created_at).toLocaleString()}</span>
                </div>
              ))
            )}
          </div>
        </section>

        <section className="events-section">
          <h2>Recent Events</h2>
          <div className="events-list">
            {events.length === 0 ? (
              <p className="empty-state">No events found.</p>
            ) : (
              events.map((event) => (
                <div key={event.id} className="event-item">
                  <div className="event-header">
                    <span className="event-type">{event.event_type}</span>
                    <span className="event-id">{event.external_id.substring(0, 7)}</span>
                  </div>
                  <p className="event-content">{event.content}</p>
                  <span className="timestamp">{new Date(event.created_at).toLocaleString()}</span>
                </div>
              ))
            )}
          </div>
        </section>
      </main>
    </div>
  )
}

export default App
