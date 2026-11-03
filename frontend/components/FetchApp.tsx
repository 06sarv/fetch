'use client'
import { FormEvent, useEffect, useState } from 'react'

type Event = { agent: string; message: string }
type Job = { id: string; status: string; objective: string; events: Event[]; proposal?: Record<string, unknown> | null; error?: string | null }
type ChatMessage = { role: 'fetch' | 'user'; text: string }
const API = process.env.NEXT_PUBLIC_API_URL || ''
const agents = ['Otto', 'Scout', 'Forger', 'Engineer', 'Assembler']

async function readResponse(response: Response): Promise<Job & { detail?: string }> {
  const text = await response.text()
  try { return JSON.parse(text) as Job & { detail?: string } }
  catch { throw new Error(`API returned HTTP ${response.status} instead of JSON`) }
}

export default function FetchApp() {
  const [objective, setObjective] = useState('Prepare a dataset to predict student final scores from study habits and attendance.')
  const [file, setFile] = useState<File | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [selectedColumns, setSelectedColumns] = useState<string[]>([])
  const [targetColumn, setTargetColumn] = useState('')
  const [chatOpen, setChatOpen] = useState(false)
  const [chatInput, setChatInput] = useState('')
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([])
  const [chatAnswers, setChatAnswers] = useState<string[]>([])
  const [chatLoading, setChatLoading] = useState(false)
  const [clarificationComplete, setClarificationComplete] = useState(false)
  const [sourceBusy, setSourceBusy] = useState('')

  function startConversation() {
    setChatOpen(true); setChatAnswers([]); setChatMessages([]); setClarificationComplete(false)
    void askOtto([])
  }

  async function askOtto(answers: string[], priorMessages: ChatMessage[] = []) {
    setChatLoading(true); setError('')
    try {
      const response = await fetch(`${API}/api/clarify`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ objective, answers }) })
      const result = await response.json()
      if (!response.ok) throw new Error(result.detail || 'Otto could not clarify the objective')
      const speaker = result.provider ? `Otto · ${result.provider}` : 'Otto'
      setChatMessages([...priorMessages, { role: 'fetch', text: result.ready ? 'That is enough detail. I’ll use your answers to guide the search and dataset preparation.' : result.question }])
      setClarificationComplete(!!result.ready)
      if (result.provider) setChatMessages([...priorMessages, { role: 'fetch', text: `${speaker}: ${result.ready ? 'I have enough detail to proceed.' : result.question}` }])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Otto could not clarify the objective')
    } finally { setChatLoading(false) }
  }

  function answerConversation() {
    const answer = chatInput.trim()
    if (!answer || chatLoading || clarificationComplete) return
    const answers = [...chatAnswers, answer]
    const messages: ChatMessage[] = [...chatMessages, { role: 'user', text: answer }]
    setChatAnswers(answers); setChatInput(''); setChatMessages(messages)
    void askOtto(answers, messages)
  }

  function enrichedObjective() {
    return chatAnswers.length ? `${objective}\n\nAdditional dataset requirements from the Fetch conversation:\n${chatAnswers.map((answer, i) => `${i + 1}. ${answer}`).join('\n')}` : objective
  }

  useEffect(() => {
    const cols = (job?.proposal?.candidate_columns as {name:string; selected:boolean}[] | undefined) || []
    if (cols.length) {
      const selected = cols.filter(c => c.selected).map(c => c.name)
      setSelectedColumns(selected)
      const hint = String(job?.proposal?.target_hint || '').toLowerCase()
      const target = cols.find(c => c.name.toLowerCase() === hint || (hint && c.name.toLowerCase().includes(hint)))?.name
        || cols.find(c => ['target','label','class','outcome','y','final_score','churn','sentiment'].includes(c.name.toLowerCase()))?.name
        || selected[selected.length - 1] || cols[cols.length - 1].name
      setTargetColumn(target)
    }
  }, [job?.id, job?.proposal?.candidate_columns, job?.proposal?.target_hint, job?.proposal?.target])

  const jobId = job?.id
  const jobStatus = job?.status
  useEffect(() => {
    if (!jobId || !['queued', 'running'].includes(jobStatus || '')) return
    let active = true
    const refresh = async () => {
      try {
        const response = await fetch(`${API}/api/jobs/${jobId}`, { cache: 'no-store' })
        if (active && response.ok) setJob(await response.json())
      } catch { /* Keep the current progress visible during a brief network interruption. */ }
    }
    void refresh()
    const timer = window.setInterval(refresh, 1500)
    return () => { active = false; window.clearInterval(timer) }
  }, [jobId, jobStatus])

  async function submit(e: FormEvent) {
    e.preventDefault(); setError('')
    if (!clarificationComplete) {
      if (!chatOpen) startConversation()
      setError('Please complete Otto’s objective conversation before starting the dataset workflow.')
      return
    }
    setBusy(true)
    try {
      const data = new FormData(); data.set('objective', enrichedObjective()); if (file) data.set('file', file)
      const response = await fetch(`${API}/api/jobs`, { method: 'POST', body: data })
      const result = await readResponse(response)
      if (!response.ok) throw new Error(result.detail || 'Could not start workflow')
      setJob(result)
    } catch (err) { setError(err instanceof Error ? err.message : 'Unexpected error') }
    finally { setBusy(false) }
  }

  async function review(approved: boolean) {
    if (!job) return
    const response = await fetch(`${API}/api/jobs/${job.id}/review`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ approved }) })
    const result = await readResponse(response)
    if (!response.ok) setError(result.detail || 'Review could not be saved'); else setJob(result)
  }

  async function confirmColumns() {
    if (!job) return
    setError('')
    const response = await fetch(`${API}/api/jobs/${job.id}/columns`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ columns: selectedColumns, target: targetColumn }) })
    const result = await readResponse(response)
    if (!response.ok) setError(result.detail || 'Column selection could not be saved'); else setJob(result)
  }

  async function loadSource(url: string) {
    if (!job) return
    setSourceBusy(url); setError('')
    try {
      const response = await fetch(`${API}/api/jobs/${job.id}/source`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url }) })
      const result = await readResponse(response)
      if (!response.ok) throw new Error(result.detail || 'Forger could not load this source')
      setJob(result)
    } catch (err) { setError(err instanceof Error ? err.message : 'Forger could not load this source') }
    finally { setSourceBusy('') }
  }

  return <main className="shell">
    <header className="top"><a className="brand" style={{fontSize: '32px'}} href="#">fetch<span className="dot">.</span></a><span className="top-tag">GOOD DATA, FETCHED.</span></header>
    <section className="hero"><div className="hero-copy"><h1>Tell us what<br/>you want to <em>learn.</em></h1><p>Your data companion sniffs out useful data, tidies it up, and brings it back for your review.</p></div><div className="mascot-card" aria-label="Fetch the beagle, your data companion"><svg className="beagle" viewBox="0 0 220 190" role="img" aria-label="A friendly beagle carrying a dataset"><ellipse cx="110" cy="171" rx="74" ry="10" fill="#dbe8d9"/><path d="M61 119c-2-30 17-51 49-51s52 21 49 51l-8 36H70z" fill="#f2c98b"/><path d="M73 87c-12-18-31-19-39-7-10 15 1 46 20 51 12 3 22-6 27-20z" fill="#8e573b"/><path d="M147 87c12-18 31-19 39-7 10 15-1 46-20 51-12 3-22-6-27-20z" fill="#8e573b"/><path d="M69 90c7-20 22-30 41-30s34 10 41 30l-7 29c-5 19-18 31-34 31s-29-12-34-31z" fill="#fff0d8"/><path d="M88 67c1-19 11-29 22-29s21 10 22 29c-12 8-32 8-44 0" fill="#8e573b"/><ellipse cx="94" cy="96" rx="4" ry="5" fill="#30271f"/><ellipse cx="126" cy="96" rx="4" ry="5" fill="#30271f"/><ellipse cx="110" cy="111" rx="8" ry="6" fill="#30271f"/><path d="M110 117c-1 9-9 12-14 7m14-7c1 9 9 12 14 7" fill="none" stroke="#654536" strokeWidth="2.5" strokeLinecap="round"/><path d="M78 148l-4 21m68-21 4 21" stroke="#8e573b" strokeWidth="10" strokeLinecap="round"/><path d="M68 123c19-8 31 0 42 10 11-10 23-18 42-10l-8 19c-18-6-25-1-34 9-9-10-16-15-34-9z" fill="#f8faf5" stroke="#dbe4d9" strokeWidth="2"/><path d="M98 137l12-7 12 7-2 15h-20z" fill="#79a982"/><path d="M103 141h14v2h-14zm0 5h14v2h-14z" stroke="#fff" strokeWidth="2"/></svg><span className="mascot-note">Fetch is ready to help</span><span className="sparkle sparkle-one">✦</span><span className="sparkle sparkle-two">✦</span></div></section>
    <div className="layout">
      <section className="panel request"><div className="panel-head"><div><h2>Describe your objective</h2><p>Start with the outcome you want to predict or understand.</p></div></div>
        <form onSubmit={submit}><label htmlFor="objective">MACHINE LEARNING OBJECTIVE</label><textarea id="objective" value={objective} onChange={e => { setObjective(e.target.value); setClarificationComplete(false); setChatOpen(false); setChatAnswers([]); setChatMessages([]) }} minLength={12} maxLength={2000} required placeholder="e.g. Predict customer churn from account activity and support history."/><div className="field-row"><span>Otto will clarify the outcome and data source first.</span><span>{objective.length}/2,000</span></div>
          <button type="button" className="conversation-trigger" onClick={startConversation}>{chatOpen ? 'Guided conversation active' : 'Tell Fetch more about your dataset →'}</button>
          {chatOpen && <div className="conversation" aria-label="Dataset requirements conversation"><div className="conversation-messages">{chatMessages.map((message, i) => <div className={`message ${message.role}`} key={`${message.role}-${i}`}><b>{message.role === 'fetch' ? 'Otto' : 'You'}</b><span>{message.text}</span></div>)}</div>{chatLoading ? <small>Otto is reviewing your objective…</small> : !clarificationComplete ? <div className="conversation-input"><input value={chatInput} onChange={e => setChatInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); answerConversation() } }} placeholder="Type your answer…"/><button type="button" onClick={answerConversation} disabled={!chatInput.trim()}>Send</button></div> : <small className="conversation-done">Otto has enough detail. You can start the workflow.</small>}</div>}
          <label className="upload-label" htmlFor="file">ADD A DATASET <span>OPTIONAL · CSV, JSON, JSONL, XLSX, TXT · UP TO 25 MB</span></label><label className="drop" htmlFor="file"><span className="upload-icon">↑</span><span><strong>{file ? file.name : 'Choose a tabular file'}</strong><small>{file ? `${(file.size / 1024).toFixed(0)} KB · ready to upload` : 'CSV, JSON, JSONL, XLSX or delimited TXT'}</small></span><span className="browse">Browse</span></label><input id="file" type="file" accept=".csv,.json,.jsonl,.ndjson,.xlsx,.txt,text/csv,application/json" onChange={e => setFile(e.target.files?.[0] || null)} hidden/>
          <p className="privacy-note">Configured AI services receive your objective and column summaries. Firecrawl receives the objective for search. Raw dataset rows stay on this computer.</p><button className="primary" disabled={busy || chatLoading || !objective.trim()}>{busy ? 'Starting workflow…' : clarificationComplete ? 'Search sources & build dataset' : 'Clarify with Otto first'} <span>↗</span></button>{error && <p className="error">{error}</p>}
        </form>
      </section>
      <section className="panel progress"><div className="panel-head"><div><h2>Agent workflow</h2><p>Five specialists work through your request.</p></div></div><div className="agents">{agents.map((name, i) => { const event = job?.events?.find(e => e.agent === name); const active = job?.status === 'running' && (i === (job.events?.length || 0)); const done = !!event; return <div className={`agent ${done ? 'done' : active ? 'active' : ''}`} key={name}><div className="agent-icon">{done ? '✓' : `0${i + 1}`}</div><div className="agent-copy"><strong>{name}</strong><small>{event?.message || ['Understands your ML goal', 'Profiles data and evaluates sources', 'Finds data or creates a fallback', 'Cleans and transforms columns', 'Packages the reviewed dataset'][i]}</small></div><span className="agent-state">{done ? 'DONE' : active ? 'WORKING' : 'QUEUED'}</span></div>})}</div>{job && <div className="job-status"><span>JOB {job.id.slice(0, 8).toUpperCase()}</span><b>{job.status.replace('_', ' ')}</b></div>}</section>
    </div>
    {job?.proposal && <section className="panel review"><div className="panel-head"><span className="step">03</span><div><h2>{job.status === 'awaiting_columns' ? 'Choose useful columns' : 'Review dataset proposal'}</h2><p>{job.status === 'awaiting_columns' ? 'Confirm the features and target before Fetch cleans the data.' : 'Check the generated preview before approving exports.'}</p></div><span className="source-badge">{job.proposal.source === 'synthetic_demo' ? 'SYNTHETIC DATASET' : String(job.proposal.source || '').replace('_', ' ')}</span></div>
      {job.proposal.source === 'synthetic_demo' && <div className="notice">This is generated demo data, not retrieved from a source listing or real-world evidence. {/(hiv|aids|human immunodeficiency)/i.test(job.objective) ? 'The medical fields are illustrative only: they are not real patient records, clinically valid measurements, or evidence for health decisions.' : 'Use it to try the workflow; select a public source below to use retrieved rows instead.'}</div>}
      {job.status === 'awaiting_columns' && <>{!!(job.proposal.discovery_results as unknown[] | undefined)?.length && <div className="discovery"><strong>Closest public dataset sources</strong><p>Choose “Load this data” to let Forger scrape the listing and retrieve a public tabular file. Some sites require sign-in or manual download.</p>{(job.proposal.discovery_results as {title:string;url:string;description:string;match_score:number}[]).map(hit => <div className="source-result" key={hit.url}><a href={hit.url} target="_blank" rel="noreferrer"><span><b>{hit.title || hit.url}</b><small>{hit.description}</small></span><em>{hit.match_score}% match ↗</em></a><button type="button" className="secondary" disabled={!!sourceBusy} onClick={() => loadSource(hit.url)}>{sourceBusy === hit.url ? 'Forger is retrieving…' : 'Load this data'}</button></div>)}</div>}<div className="candidate-list">{((job.proposal.candidate_columns as {name:string;dtype:string;missing:number;unique:number;relevance:string;selected:boolean}[]) || []).map(c => <label className="candidate" key={c.name}><input type="checkbox" checked={selectedColumns.includes(c.name)} onChange={e => setSelectedColumns(v => e.target.checked ? [...v,c.name] : v.filter(x => x !== c.name))}/><span><strong>{c.name}</strong><small>{c.dtype} · {c.missing} missing · {c.unique} unique · {c.relevance}</small></span></label>)}</div><label className="target-pick">PREDICTION TARGET<select value={targetColumn} onChange={e => setTargetColumn(e.target.value)}>{selectedColumns.map(c => <option key={c} value={c}>{c}</option>)}</select></label><div className="review-actions"><span>{String(job.proposal.experience_level || 'novice')} view · {String(job.proposal.candidate_rows || 0)} candidate rows</span><button className="primary compact" disabled={!selectedColumns.length || !selectedColumns.includes(targetColumn)} onClick={confirmColumns}>Confirm columns &amp; prepare →</button></div></>}
      {job.status !== 'awaiting_columns' && <>
      <div className="stats"><div><small>ROWS</small><strong>{String(job.proposal.rows ?? '—')}</strong></div><div><small>COLUMNS</small><strong>{String(job.proposal.columns_count ?? '—')}</strong></div><div><small>TARGET</small><strong>{String(job.proposal.target ?? '—')}</strong></div><div><small>MISSING VALUES</small><strong>{String(job.proposal.missing_after ?? '—')}</strong></div></div>
      <div className="table-wrap"><table><thead><tr>{((job.proposal.columns as string[]) || []).map(c => <th key={c}>{c}</th>)}</tr></thead><tbody>{((job.proposal.preview as Record<string, unknown>[]) || []).slice(0, 6).map((row, i) => <tr key={i}>{((job.proposal!.columns as string[]) || []).map(c => <td key={c}>{String(row[c] ?? '')}</td>)}</tr>)}</tbody></table></div>
      {job.status === 'awaiting_review' && <div className="review-actions"><span>Approve to unlock dataset and script downloads.</span><button className="secondary" onClick={() => review(false)}>Reject</button><button className="primary compact" onClick={() => review(true)}>Approve dataset ↗</button></div>}
      {job.status === 'completed' && <div className="downloads"><strong>Dataset approved — your files are ready.</strong>{['dataset.csv','dataset.json','prepare.py'].map(name => <a href={`${API}/api/jobs/${job.id}/artifacts/${name}`} key={name} download>{name} ↓</a>)}</div>}
      </>}
      {job.error && <p className="error">{job.error}</p>}
    </section>}
    <footer><span>Designed for thoughtful data work.</span><span>Fetch · Preview before you trust.</span></footer>
  </main>
}
