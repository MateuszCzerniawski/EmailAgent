/**
 * @file SettingsPage — runtime configuration UI for all application settings.
 *
 * Sections:
 *   - LLM Provider: toggle between OpenAI (cloud) and Ollama (local). Ollama section shows
 *     detected installed models from GET /config/ollama/models and a collapsible setup guide.
 *   - Email sync: test Gmail connectivity, view sync status.
 *   - Vector database: trigger a full refresh (re-embed all emails), view per-collection stats,
 *     and search the knowledge base interactively.
 *   - Guidelines & Tags: create new guideline documents and predefined tags.
 *   - Danger zone: clear all localStorage overrides.
 *
 * Settings that are local to the browser (modelMode, sttLang, sttDeviceId) are persisted via
 * readSettings() / writeSettings() from storage.js.
 * Server-side LLM config is persisted by PUT /config/llm → config/config.yaml.
 */

import React from 'react'
import { Button } from '../components/Button'
import { Badge } from '../components/Badge'
import { Card, CardHeader, CardSection } from '../components/Card'
import { Alert } from '../components/Alert'
import { InfoBox } from '../components/InfoBox'
import { Input, Select, FormField, Label } from '../components/Form'
import { SkeletonLine } from '../components/Skeleton'
import { readSettings, writeSettings, readOverridesMap, writeOverridesMap } from '../tools/storage'
import {
    createGuideline,
    createTag,
    getAuthStatus,
    getVectorSyncReport,
    getLlmConfig,
    updateLlmConfig,
    getOllamaGuide,
    getOllamaModels,
    refreshVectorDatabase,
    searchKnowledgeBase,
    summarizeRefreshReport,
    summarizeSyncReport,
    testEmailsEndpoint,
} from '../../api/client'

function baseUrl() {
    return import.meta.env?.VITE_API_BASE_URL || 'http://127.0.0.1:8001'
}

export function SettingsPage() {
    const [settings, setSettings] = React.useState(() => readSettings())
    const [overrideCount, setOverrideCount] = React.useState(() => Object.keys(readOverridesMap()).length)
    const [connStatus, setConnStatus] = React.useState('idle')
    const [connResult, setConnResult] = React.useState(null)
    const [authStatus, setAuthStatus] = React.useState('loading')
    const [authData, setAuthData] = React.useState(null)
    const [metricsStatus, setMetricsStatus] = React.useState('loading')
    const [syncReport, setSyncReport] = React.useState(null)
    const [refreshReport, setRefreshReport] = React.useState(null)
    const [metricsError, setMetricsError] = React.useState('')
    const [tagName, setTagName] = React.useState('')
    const [guidelinePath, setGuidelinePath] = React.useState('')
    const [tagStatus, setTagStatus] = React.useState('idle')
    const [guidelineStatus, setGuidelineStatus] = React.useState('idle')
    const [refreshStatus, setRefreshStatus] = React.useState('idle')
    const [tagSuccess, setTagSuccess] = React.useState('')
    const [guidelineSuccess, setGuidelineSuccess] = React.useState('')
    const [refreshSuccess, setRefreshSuccess] = React.useState('')
    const [adminError, setAdminError] = React.useState('')
    const [searchQuery, setSearchQuery] = React.useState('')
    const [searchTopK, setSearchTopK] = React.useState(5)
    const [searchStatus, setSearchStatus] = React.useState('idle')
    const [searchResults, setSearchResults] = React.useState([])
    const [searchError, setSearchError] = React.useState('')
    const [audioDevices, setAudioDevices] = React.useState([])
    const [micAccessStatus, setMicAccessStatus] = React.useState('idle') // idle | requesting | denied

    const [llmProvider, setLlmProvider] = React.useState('openai')
    const [llmOpenaiModel, setLlmOpenaiModel] = React.useState('gpt-4o-mini')
    const [llmOllamaModel, setLlmOllamaModel] = React.useState('qwen2.5:7b')
    const [llmOllamaBaseUrl, setLlmOllamaBaseUrl] = React.useState('http://localhost:11434')
    const [llmLoadStatus, setLlmLoadStatus] = React.useState('loading')
    const [llmSaveStatus, setLlmSaveStatus] = React.useState('idle')
    const [llmSaveError, setLlmSaveError] = React.useState('')
    const [guideText, setGuideText] = React.useState(null)
    const [ollamaModels, setOllamaModels] = React.useState([])
    const [ollamaRunning, setOllamaRunning] = React.useState(null)
    const [ollamaCheckStatus, setOllamaCheckStatus] = React.useState('idle')

    const syncSummary = React.useMemo(() => summarizeSyncReport(syncReport), [syncReport])
    const refreshSummary = React.useMemo(() => summarizeRefreshReport(refreshReport), [refreshReport])

    React.useEffect(() => {
        navigator.mediaDevices?.enumerateDevices()
            .then(devices => setAudioDevices(devices.filter(d => d.kind === 'audioinput')))
            .catch(() => {})
    }, [])

    async function requestMicAccess() {
        setMicAccessStatus('requesting')
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
            stream.getTracks().forEach(t => t.stop())
            const devices = await navigator.mediaDevices.enumerateDevices()
            setAudioDevices(devices.filter(d => d.kind === 'audioinput'))
            setMicAccessStatus('idle')
        } catch {
            setMicAccessStatus('denied')
        }
    }

    function setModelMode(modelMode) {
        const next = { ...settings, modelMode }
        setSettings(next)
        writeSettings(next)
    }

    function setSttLang(sttLang) {
        const next = { ...settings, sttLang }
        setSettings(next)
        writeSettings(next)
    }

    function setSttDevice(sttDeviceId) {
        const next = { ...settings, sttDeviceId: sttDeviceId || null }
        setSettings(next)
        writeSettings(next)
    }

    function clearOverrides() {
        writeOverridesMap({})
        setOverrideCount(0)
    }

    async function checkOllama() {
        setOllamaCheckStatus('loading')
        try {
            const res = await getOllamaModels()
            setOllamaRunning(res.running)
            setOllamaModels(res.models || [])
        } catch {
            setOllamaRunning(false)
            setOllamaModels([])
        } finally {
            setOllamaCheckStatus('done')
        }
    }

    async function loadLlmConfig({ signal } = {}) {
        setLlmLoadStatus('loading')
        try {
            const cfg = await getLlmConfig({ signal })
            setLlmProvider(cfg.provider || 'openai')
            setLlmOpenaiModel(cfg.openai_model || 'gpt-4o-mini')
            setLlmOllamaModel(cfg.ollama_model || 'qwen2.5:7b')
            setLlmOllamaBaseUrl(cfg.ollama_base_url || 'http://localhost:11434')
            setLlmLoadStatus('success')
            if (cfg.provider === 'ollama') checkOllama()
        } catch (e) {
            if (e?.name === 'AbortError') return
            setLlmLoadStatus('error')
        }
    }

    async function handleSaveLlmConfig(event) {
        event.preventDefault()
        setLlmSaveError('')
        setLlmSaveStatus('loading')
        try {
            await updateLlmConfig({
                provider: llmProvider,
                openai_model: llmOpenaiModel,
                ollama_model: llmOllamaModel,
                ollama_base_url: llmOllamaBaseUrl,
            })
            setLlmSaveStatus('success')
        } catch (e) {
            setLlmSaveStatus('error')
            setLlmSaveError(String(e?.message || e))
        }
    }

    async function runConnectionTest() {
        setConnStatus('loading')
        setConnResult(null)
        try {
            const res = await testEmailsEndpoint()
            setConnResult({ ok: true, sample: res })
            setConnStatus('ok')
        } catch (e) {
            setConnResult({ ok: false, message: String(e?.message || e), status: e?.status, body: e?.body })
            setConnStatus('error')
        }
    }

    async function loadAuth({ signal } = {}) {
        setAuthStatus('loading')
        try {
            const res = await getAuthStatus({ signal })
            setAuthData(res)
            setAuthStatus('success')
        } catch (e) {
            if (e?.name === 'AbortError') return
            setAuthStatus('error')
            setAuthData(null)
        }
    }

    async function loadMetrics({ signal } = {}) {
        setMetricsStatus('loading')
        setMetricsError('')
        try {
            const res = await getVectorSyncReport({ signal })
            setSyncReport(res)
            setMetricsStatus('success')
        } catch (e) {
            if (e?.name === 'AbortError') return
            setMetricsStatus('error')
            setMetricsError(String(e?.message || e))
        }
    }

    async function handleCreateTag(event) {
        event.preventDefault()
        setAdminError('')
        setTagSuccess('')
        setTagStatus('loading')
        try {
            const created = await createTag({ name: tagName.trim() })
            setTagSuccess(`Tag "${created.name}" added successfully.`)
            setTagName('')
            setTagStatus('success')
        } catch (e) {
            setTagStatus('error')
            setAdminError(String(e?.message || e))
        }
    }

    async function handleCreateGuideline(event) {
        event.preventDefault()
        setAdminError('')
        setGuidelineSuccess('')
        setGuidelineStatus('loading')
        try {
            const created = await createGuideline({ file_path: guidelinePath.trim() })
            setGuidelineSuccess(`Guideline "${created.filename}" ingested successfully.`)
            setGuidelinePath('')
            setGuidelineStatus('success')
            loadMetrics({})
        } catch (e) {
            setGuidelineStatus('error')
            setAdminError(String(e?.message || e))
        }
    }

    async function handleRefreshVectors() {
        setAdminError('')
        setRefreshSuccess('')
        setRefreshStatus('loading')
        try {
            const res = await refreshVectorDatabase()
            setRefreshReport(res)
            setRefreshSuccess(`Refresh completed. Re-embedded ${summarizeRefreshReport(res).totalReembedded} records.`)
            setRefreshStatus('success')
            loadMetrics({})
        } catch (e) {
            setRefreshStatus('error')
            setAdminError(String(e?.message || e))
        }
    }

    async function handleSearch(event) {
        event.preventDefault()
        setSearchError('')
        setSearchStatus('loading')
        try {
            const results = await searchKnowledgeBase({
                query: searchQuery.trim(),
                topK: Number(searchTopK) || 5,
            })
            setSearchResults(Array.isArray(results) ? results : [])
            setSearchStatus('success')
        } catch (e) {
            setSearchStatus('error')
            setSearchResults([])
            setSearchError(String(e?.message || e))
        }
    }

    React.useEffect(() => {
        const controller = new AbortController()
        loadAuth({ signal: controller.signal })
        loadMetrics({ signal: controller.signal })
        loadLlmConfig({ signal: controller.signal })
        const id = window.setInterval(() => setOverrideCount(Object.keys(readOverridesMap()).length), 1000)
        return () => {
            controller.abort()
            window.clearInterval(id)
        }
    }, [])

    return (
        <div className="space-y-4">
            <Card>
                <CardHeader
                    title="Settings"
                    description="Admin tools for Gmail auth, RAG ingestion, vector refresh, and local UI preferences."
                />
            </Card>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
                <section className="space-y-4 lg:col-span-7">
                    <Card>
                        <CardHeader
                            title="Gmail authorization"
                            description="The inbox depends on a connected Gmail account."
                            actions={
                                <div className="flex items-center gap-2">
                                    {authStatus === 'loading' ? (
                                        <SkeletonLine width="w-24" height="h-6" className="rounded-full" />
                                    ) : (
                                        <Badge tone={authData?.authenticated ? 'green' : 'amber'}>
                                            {authData?.authenticated ? 'Authenticated' : 'Not connected'}
                                        </Badge>
                                    )}
                                    <Button as="a" href={`${baseUrl()}/auth/gmail`} variant="secondary" size="sm">
                                        Connect Gmail
                                    </Button>
                                </div>
                            }
                        />
                        {authStatus === 'error' && (
                            <Alert variant="warning">Couldn't load Gmail auth status.</Alert>
                        )}
                    </Card>

                    <Card>
                        <CardHeader
                            title="RAG administration"
                            description="Create reusable tags and ingest guideline files for retrieval."
                        />
                        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                            <form onSubmit={handleCreateTag} className="space-y-3">
                                <Label>Add tag</Label>
                                <FormField label="Tag name">
                                    <Input
                                        value={tagName}
                                        onChange={(e) => setTagName(e.target.value)}
                                        placeholder="e.g. compliance"
                                        disabled={tagStatus === 'loading'}
                                    />
                                </FormField>
                                <div className="flex items-center gap-2">
                                    <Button variant="primary" type="submit" disabled={!tagName.trim() || tagStatus === 'loading'}>
                                        {tagStatus === 'loading' ? 'Saving…' : 'Save tag'}
                                    </Button>
                                    <Button variant="ghost" type="button" onClick={() => setTagName('')} disabled={tagStatus === 'loading'}>
                                        Cancel
                                    </Button>
                                </div>
                                {tagStatus === 'loading' && <SkeletonLine height="h-10" />}
                            </form>

                            <form onSubmit={handleCreateGuideline} className="space-y-3">
                                <Label>Add guideline</Label>
                                <FormField label="File path">
                                    <Input
                                        value={guidelinePath}
                                        onChange={(e) => setGuidelinePath(e.target.value)}
                                        placeholder="D:\\docs\\policy.md"
                                        disabled={guidelineStatus === 'loading'}
                                    />
                                </FormField>
                                <div className="flex items-center gap-2">
                                    <Button variant="primary" type="submit" disabled={!guidelinePath.trim() || guidelineStatus === 'loading'}>
                                        {guidelineStatus === 'loading' ? 'Ingesting…' : 'Save guideline'}
                                    </Button>
                                    <Button variant="ghost" type="button" onClick={() => setGuidelinePath('')} disabled={guidelineStatus === 'loading'}>
                                        Cancel
                                    </Button>
                                </div>
                                {guidelineStatus === 'loading' && <SkeletonLine height="h-10" />}
                            </form>
                        </div>
                        <CardSection className="space-y-2">
                            {tagSuccess ? <Alert variant="success">{tagSuccess}</Alert> : null}
                            {guidelineSuccess ? <Alert variant="success">{guidelineSuccess}</Alert> : null}
                            {adminError ? <Alert variant="error">{adminError}</Alert> : null}
                        </CardSection>
                    </Card>

                    <Card>
                        <CardHeader
                            title="RAG document search"
                            description="Search the knowledge base and inspect the returned snippets."
                        />
                        <form onSubmit={handleSearch} className="space-y-3">
                            <div className="grid grid-cols-1 gap-3 md:grid-cols-12">
                                <FormField label="Query" className="md:col-span-9">
                                    <Input
                                        value={searchQuery}
                                        onChange={(e) => setSearchQuery(e.target.value)}
                                        placeholder="Ask about escalation policies or onboarding steps"
                                        disabled={searchStatus === 'loading'}
                                    />
                                </FormField>
                                <FormField label="Top K" className="md:col-span-3">
                                    <Input
                                        type="number"
                                        min="1"
                                        max="50"
                                        value={searchTopK}
                                        onChange={(e) => setSearchTopK(e.target.value)}
                                        disabled={searchStatus === 'loading'}
                                    />
                                </FormField>
                            </div>
                            <div className="flex items-center gap-2">
                                <Button variant="primary" type="submit" disabled={!searchQuery.trim() || searchStatus === 'loading'}>
                                    {searchStatus === 'loading' ? 'Searching…' : 'Search'}
                                </Button>
                                <Button
                                    variant="ghost"
                                    type="button"
                                    onClick={() => {
                                        setSearchQuery('')
                                        setSearchResults([])
                                        setSearchError('')
                                        setSearchStatus('idle')
                                    }}
                                >
                                    Clear
                                </Button>
                            </div>
                        </form>
                        <CardSection className="space-y-3">
                            {searchStatus === 'loading' &&
                                Array.from({ length: 3 }).map((_, idx) => (
                                    <div key={idx} className="space-y-2">
                                        <SkeletonLine width="w-40" />
                                        <SkeletonLine height="h-16" />
                                    </div>
                                ))}
                            {searchError ? <Alert variant="error">{searchError}</Alert> : null}
                            {searchStatus === 'success' && searchResults.length === 0 ? (
                                <InfoBox>No matching documents found.</InfoBox>
                            ) : null}
                            {searchResults.map((result, idx) => (
                                <div key={`${result.source}-${idx}`} className="space-y-2 rounded-xl border border-slate-200 p-3">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <Badge tone="blue">{result.source}</Badge>
                                        <Badge tone="violet">score {Number(result.score || 0).toFixed(3)}</Badge>
                                    </div>
                                    <InfoBox>{result.document || '(No snippet)'}</InfoBox>
                                    <pre className="overflow-auto rounded-lg border border-slate-200 bg-slate-50 p-3 text-[11px] text-slate-700">
                                        {JSON.stringify(result.metadata || {}, null, 2)}
                                    </pre>
                                </div>
                            ))}
                        </CardSection>
                    </Card>
                </section>

                <aside className="space-y-4 lg:col-span-5">
                    <Card>
                        <CardHeader
                            title="Vector database metrics"
                            description="Sync and refresh reports from the RAG pipeline."
                            actions={
                                <div className="flex items-center gap-2">
                                    <Button variant="secondary" size="sm" onClick={() => loadMetrics({})} disabled={metricsStatus === 'loading'}>
                                        {metricsStatus === 'loading' ? 'Loading…' : 'Reload'}
                                    </Button>
                                    <Button variant="primary" size="sm" onClick={handleRefreshVectors} disabled={refreshStatus === 'loading'}>
                                        {refreshStatus === 'loading' ? 'Refreshing…' : 'Refresh vectors'}
                                    </Button>
                                </div>
                            }
                        />
                        <CardSection className="space-y-3">
                            {metricsStatus === 'loading' ? (
                                <>
                                    <SkeletonLine height="h-16" />
                                    <SkeletonLine height="h-16" />
                                </>
                            ) : metricsError ? (
                                <Alert variant="error">{metricsError}</Alert>
                            ) : (
                                <>
                                    <InfoBox>
                                        <div className="flex items-center justify-between gap-3">
                                            <div className="font-medium text-slate-900">Sync report</div>
                                            <Badge tone={syncSummary.healthy ? 'green' : 'amber'}>
                                                {syncSummary.healthy ? 'Healthy' : 'Attention needed'}
                                            </Badge>
                                        </div>
                                        <div className="mt-2 space-y-1 text-sm">
                                            <div>Postgres emails: {syncSummary.total_in_postgres}</div>
                                            <div>Chroma docs: {syncSummary.total_in_chroma}</div>
                                            <div>Missing in Chroma: {syncSummary.missingCount}</div>
                                            <div>Orphaned in Chroma: {syncSummary.orphanedCount}</div>
                                        </div>
                                    </InfoBox>
                                    <InfoBox>
                                        <div className="font-medium text-slate-900">Refresh report</div>
                                        <div className="mt-2 space-y-1 text-sm">
                                            <div>Emails re-embedded: {refreshSummary.emails_reembedded}</div>
                                            <div>Guidelines re-embedded: {refreshSummary.guidelines_reembedded}</div>
                                            <div>Errors: {refreshSummary.errorCount}</div>
                                        </div>
                                    </InfoBox>
                                    {syncSummary.missingCount > 0 ? (
                                        <InfoBox className="text-xs">
                                            Missing IDs: {syncSummary.missing_in_chroma.slice(0, 5).join(', ')}
                                        </InfoBox>
                                    ) : null}
                                    {refreshSummary.errorCount > 0 ? (
                                        <InfoBox className="text-xs">
                                            Errors: {refreshSummary.errors.slice(0, 3).join(' | ')}
                                        </InfoBox>
                                    ) : null}
                                </>
                            )}
                            {refreshSuccess ? <Alert variant="success">{refreshSuccess}</Alert> : null}
                            {adminError && refreshStatus === 'error' ? <Alert variant="error">{adminError}</Alert> : null}
                        </CardSection>
                    </Card>

                    <Card>
                        <CardHeader
                            title="Backend connection"
                            description={
                                <>
                                    <div>
                                        Base URL: <span className="font-mono text-xs">{baseUrl()}</span>
                                    </div>
                                    <div className="mt-1 text-xs text-slate-500">
                                        Configure with <span className="font-mono">VITE_API_BASE_URL</span>.
                                    </div>
                                </>
                            }
                        />
                        <CardSection>
                            <div className="flex items-center gap-2">
                                <Button variant="secondary" size="sm" onClick={runConnectionTest} disabled={connStatus === 'loading'}>
                                    {connStatus === 'loading' ? 'Testing…' : 'Test /emails'}
                                </Button>
                                {connStatus === 'ok' ? <Badge tone="green">OK</Badge> : null}
                                {connStatus === 'error' ? <Badge tone="rose">Error</Badge> : null}
                            </div>
                            {connResult && (
                                <pre className="mt-3 max-h-56 overflow-auto rounded-lg border border-slate-200 bg-slate-50 p-3 text-[11px] text-slate-800">
                                    {JSON.stringify(connResult, null, 2)}
                                </pre>
                            )}
                        </CardSection>
                    </Card>

                    <Card>
                        <CardHeader
                            title="LLM provider"
                            description="Switch between OpenAI and a locally-running Ollama instance."
                        />
                        {llmLoadStatus === 'loading' ? (
                            <CardSection className="space-y-3">
                                <SkeletonLine height="h-10" />
                                <SkeletonLine height="h-10" />
                            </CardSection>
                        ) : llmLoadStatus === 'error' ? (
                            <CardSection>
                                <Alert variant="warning">Could not load LLM config from backend.</Alert>
                            </CardSection>
                        ) : (
                            <form onSubmit={handleSaveLlmConfig}>
                                <CardSection className="space-y-3">
                                    <FormField label="Provider">
                                        <Select
                                            value={llmProvider}
                                            onChange={(e) => {
                                                setLlmProvider(e.target.value)
                                                setLlmSaveStatus('idle')
                                                setLlmSaveError('')
                                                if (e.target.value === 'ollama') checkOllama()
                                            }}
                                        >
                                            <option value="openai">OpenAI</option>
                                            <option value="ollama">Ollama (local)</option>
                                        </Select>
                                    </FormField>

                                    {llmProvider === 'openai' ? (
                                        <FormField label="OpenAI model">
                                            <Input
                                                value={llmOpenaiModel}
                                                onChange={(e) => setLlmOpenaiModel(e.target.value)}
                                                placeholder="gpt-4o-mini"
                                            />
                                        </FormField>
                                    ) : (
                                        <>
                                            <div className="space-y-2">
                                                <div className="flex items-center gap-2">
                                                    {ollamaCheckStatus === 'loading' && (
                                                        <SkeletonLine width="w-32" height="h-5" className="rounded-full" />
                                                    )}
                                                    {ollamaCheckStatus === 'done' && (
                                                        <Badge tone={ollamaRunning ? 'green' : 'amber'}>
                                                            {ollamaRunning ? 'Ollama running' : 'Ollama not reachable'}
                                                        </Badge>
                                                    )}
                                                    <Button
                                                        variant="ghost"
                                                        size="sm"
                                                        type="button"
                                                        onClick={checkOllama}
                                                        disabled={ollamaCheckStatus === 'loading'}
                                                    >
                                                        {ollamaCheckStatus === 'loading' ? 'Checking…' : 'Check'}
                                                    </Button>
                                                </div>

                                                <FormField label="Ollama model">
                                                    {ollamaRunning && ollamaModels.length > 0 ? (
                                                        <Select
                                                            value={llmOllamaModel}
                                                            onChange={(e) => setLlmOllamaModel(e.target.value)}
                                                        >
                                                            {ollamaModels.map((m) => (
                                                                <option key={m} value={m}>{m}</option>
                                                            ))}
                                                            {!ollamaModels.includes(llmOllamaModel) && llmOllamaModel && (
                                                                <option value={llmOllamaModel}>{llmOllamaModel} (not installed)</option>
                                                            )}
                                                        </Select>
                                                    ) : (
                                                        <Input
                                                            value={llmOllamaModel}
                                                            onChange={(e) => setLlmOllamaModel(e.target.value)}
                                                            placeholder="qwen2.5:7b"
                                                        />
                                                    )}
                                                </FormField>

                                                {ollamaCheckStatus === 'done' && !ollamaRunning && (
                                                    <Alert variant="warning">
                                                        Ollama is not reachable at {llmOllamaBaseUrl}. Start it with <code>ollama serve</code> then click Check.
                                                    </Alert>
                                                )}
                                            </div>

                                            <FormField label="Ollama base URL">
                                                <Input
                                                    value={llmOllamaBaseUrl}
                                                    onChange={(e) => setLlmOllamaBaseUrl(e.target.value)}
                                                    placeholder="http://localhost:11434"
                                                />
                                            </FormField>
                                            <details
                                                className="rounded-lg border border-slate-200"
                                                onToggle={(e) => {
                                                    if (e.target.open && !guideText) {
                                                        getOllamaGuide()
                                                            .then(setGuideText)
                                                            .catch(() => setGuideText('Failed to load guide.'))
                                                    }
                                                }}
                                            >
                                                <summary className="cursor-pointer select-none px-4 py-2 text-sm font-medium text-slate-700">
                                                    Ollama setup guide
                                                </summary>
                                                <pre className="max-h-96 overflow-auto px-4 pb-4 font-mono text-xs leading-relaxed text-slate-700 whitespace-pre-wrap">
                                                    {guideText ?? 'Loading…'}
                                                </pre>
                                            </details>
                                        </>
                                    )}

                                    <div className="flex items-center gap-2">
                                        <Button
                                            variant="primary"
                                            type="submit"
                                            disabled={llmSaveStatus === 'loading'}
                                        >
                                            {llmSaveStatus === 'loading' ? 'Saving…' : 'Save'}
                                        </Button>
                                        {llmSaveStatus === 'success' && (
                                            <Badge tone="green">Saved</Badge>
                                        )}
                                    </div>
                                    {llmSaveError ? <Alert variant="error">{llmSaveError}</Alert> : null}
                                </CardSection>
                            </form>
                        )}
                    </Card>

                    <Card>
                        <CardHeader
                            title="Speech-to-text"
                            description="Browser speech recognition language used by the reply draft editor."
                        />
                        <CardSection className="flex flex-wrap gap-2">
                            <Button variant={settings.sttLang === 'pl-PL' ? 'primary' : 'secondary'} onClick={() => setSttLang('pl-PL')}>
                                Polish
                            </Button>
                            <Button variant={settings.sttLang === 'en-US' ? 'primary' : 'secondary'} onClick={() => setSttLang('en-US')}>
                                English
                            </Button>
                        </CardSection>
                        <CardSection>
                            {audioDevices.length === 0 || audioDevices.every(d => !d.label) ? (
                                <div className="space-y-2">
                                    <InfoBox>
                                        {micAccessStatus === 'denied'
                                            ? 'Microphone access was denied. Allow it in browser site settings and try again.'
                                            : 'Microphone names require permission to display.'}
                                    </InfoBox>
                                    <Button
                                        variant="secondary"
                                        size="sm"
                                        onClick={requestMicAccess}
                                        disabled={micAccessStatus === 'requesting'}
                                    >
                                        {micAccessStatus === 'requesting' ? 'Requesting…' : 'Grant microphone access'}
                                    </Button>
                                </div>
                            ) : (
                                <FormField label="Microphone">
                                    <Select
                                        value={settings.sttDeviceId || ''}
                                        onChange={(e) => setSttDevice(e.target.value)}
                                    >
                                        <option value="">System default</option>
                                        {audioDevices.map((d, i) => (
                                            <option key={d.deviceId} value={d.deviceId}>
                                                {d.label || `Microphone ${i + 1}`}
                                            </option>
                                        ))}
                                    </Select>
                                    <p className="mt-1 text-xs text-slate-500">Browser support for microphone selection is limited; falls back to system default.</p>
                                </FormField>
                            )}
                        </CardSection>
                    </Card>

                    <Card>
                        <CardHeader
                            title="Local overrides"
                            description="Manual edits you made to AI tags, priorities, summaries, and actions."
                        />
                        <CardSection>
                            <div className="flex items-center justify-between rounded-lg border border-slate-200 bg-slate-50 p-3">
                                <div className="text-sm text-slate-700">{overrideCount} emails overridden</div>
                                <Button variant="danger" size="sm" onClick={clearOverrides} disabled={overrideCount === 0}>
                                    Clear
                                </Button>
                            </div>
                        </CardSection>
                    </Card>
                </aside>
            </div>
        </div>
    )
}
