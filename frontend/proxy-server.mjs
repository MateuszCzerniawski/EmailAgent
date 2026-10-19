import http from 'node:http'
import { URL } from 'node:url'

const BACKEND_ORIGIN = process.env.BACKEND_ORIGIN || 'http://127.0.0.1:8000'
const PORT = Number(process.env.PROXY_PORT || 8001)

const ALLOW_ORIGIN = process.env.CORS_ALLOW_ORIGIN || '*'

function setCommonCorsHeaders(res) {
  res.setHeader('Access-Control-Allow-Origin', ALLOW_ORIGIN)
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,PUT,PATCH,DELETE,OPTIONS')
  res.setHeader('Access-Control-Allow-Headers', '*')
  res.setHeader('Access-Control-Expose-Headers', 'Content-Type')
}

function clampMaxResults(reqUrl) {
  // Backend cost limiter for test mode.
  const u = new URL(reqUrl, `http://localhost:${PORT}`)
  const mr = u.searchParams.get('max_results')
  if (mr != null) {
    const n = Number(mr)
    if (Number.isFinite(n)) u.searchParams.set('max_results', String(Math.min(2, Math.max(1, n))))
    else u.searchParams.set('max_results', '2')
  }
  return u.pathname + u.search
}

async function readRequestBody(req) {
  if (req.method === 'GET' || req.method === 'HEAD') return undefined
  const chunks = []
  for await (const chunk of req) chunks.push(chunk)
  return Buffer.concat(chunks)
}

const server = http.createServer(async (req, res) => {
  try {
    setCommonCorsHeaders(res)

    if (req.method === 'OPTIONS') {
      res.writeHead(204)
      res.end()
      return
    }

    const url = req.url || '/'
    const rewrittenPath = clampMaxResults(url)
    const target = `${BACKEND_ORIGIN}${rewrittenPath}`

    const incomingHeaders = req.headers || {}
    // Avoid forwarding hop-by-hop headers that can confuse upstream.
    const headers = { ...incomingHeaders }
    delete headers.host
    delete headers.connection
    delete headers['content-length']

    const body = await readRequestBody(req)

    const backendRes = await fetch(target, {
      method: req.method,
      headers,
      body,
    })

    // Copy backend headers (best-effort) and add CORS headers.
    const backendHeaders = backendRes.headers
    const outHeaders = {}
    for (const [k, v] of backendHeaders.entries()) outHeaders[k] = v

    // Ensure CORS still applies even if backend doesn't set it.
    outHeaders['access-control-allow-origin'] = ALLOW_ORIGIN

    res.writeHead(backendRes.status, outHeaders)

    const buf = Buffer.from(await backendRes.arrayBuffer())
    res.end(buf)
  } catch (e) {
    setCommonCorsHeaders(res)
    res.writeHead(502, { 'Content-Type': 'text/plain; charset=utf-8' })
    res.end(`Proxy error: ${String(e?.message || e)}`)
  }
})

server.listen(PORT, '127.0.0.1', () => {
  // eslint-disable-next-line no-console
  console.log(`Proxy listening on http://127.0.0.1:${PORT} -> ${BACKEND_ORIGIN}`)
})

