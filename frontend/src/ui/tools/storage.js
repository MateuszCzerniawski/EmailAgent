/**
 * @file storage — thin localStorage wrappers for two namespaced keys:
 *   - `email.overrides.v1`: per-email draft edits (suggested reply, metadata) keyed by email ID
 *   - `email.settings.v1`: global UI settings (modelMode, sttLang, sttDeviceId)
 *
 * All functions are safe to call in environments where localStorage is unavailable (SSR, tests).
 */

const KEY_OVERRIDES = 'email.overrides.v1'
const KEY_SETTINGS = 'email.settings.v1'

function safeParse(json, fallback) {
  try {
    const v = JSON.parse(json)
    return v ?? fallback
  } catch {
    return fallback
  }
}

function safeStringify(value) {
  try {
    return JSON.stringify(value)
  } catch {
    return '{}'
  }
}

export function readOverridesMap() {
  if (typeof localStorage === 'undefined') return {}
  return safeParse(localStorage.getItem(KEY_OVERRIDES) || '{}', {})
}

export function writeOverridesMap(map) {
  if (typeof localStorage === 'undefined') return
  localStorage.setItem(KEY_OVERRIDES, safeStringify(map || {}))
}

export function getEmailOverrides(emailId) {
  if (!emailId) return null
  const map = readOverridesMap()
  return map[emailId] || null
}

export function setEmailOverrides(emailId, overrides) {
  if (!emailId) return
  const map = readOverridesMap()
  map[emailId] = { ...(map[emailId] || {}), ...(overrides || {}), _updatedAt: Date.now() }
  writeOverridesMap(map)
}

export function clearEmailOverrides(emailId) {
  if (!emailId) return
  const map = readOverridesMap()
  delete map[emailId]
  writeOverridesMap(map)
}

export function readSettings() {
  if (typeof localStorage === 'undefined') return { modelMode: 'cloud', sttLang: 'pl-PL', sttDeviceId: null }
  const s = safeParse(localStorage.getItem(KEY_SETTINGS) || '{}', {})
  return { modelMode: 'cloud', sttLang: 'pl-PL', sttDeviceId: null, ...s }
}

export function writeSettings(next) {
  if (typeof localStorage === 'undefined') return
  localStorage.setItem(KEY_SETTINGS, safeStringify(next || {}))
}

