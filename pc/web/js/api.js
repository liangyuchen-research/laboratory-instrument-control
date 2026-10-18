// api.js — the one way the page talks to the program on this PC (same origin, JSON both ways).
//
//   get(path)         -> Promise<object>
//   post(path, body)  -> Promise<object>     adds X-Token (page token of /api/bootstrap) and X-Unlock (maintenance
//                                            session, when there is one); after 403 “Invalid local request” the
//                                            bootstrap is read again once and the request is sent a second time
//   ApiError          {status, code, message, reasons}   message = the text for the screen, reasons = [{code, text, fix}]
// Both take an optional last argument {timeout: milliseconds}.

import { store } from './store.js';

export class ApiError extends Error {
  constructor(status, code, message, reasons) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code || '';
    this.reasons = Array.isArray(reasons) ? reasons : [];
  }
}

const NO_ANSWER = "The program did not respond";

let fixture = null;      // development aid of main.js; null in the real program
const hooks = [];        // answers given by the page itself (frame.js: acknowledging its own alarm ui_stale)

// fn(method, path, body) -> undefined (not mine) | {reply} (answered here) | {body} (go on with this body)
export function intercept(fn) { hooks.push(fn); }

// handler(method, path, body) -> reply, or throws {status, code, error, reasons}. Only main.js calls this.
export function useFixture(handler) { fixture = handler; }

function fail(status, data) {
  const body = data && typeof data === 'object' ? data : {};
  return new ApiError(status, body.code, body.error || (status ? `HTTP ${status}` : NO_ANSWER), body.reasons);
}

async function send(method, path, body, options) {
  if (fixture) {
    try {
      return await fixture(method, path, body);
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw fail(error && error.status !== undefined ? error.status : 500, error);
    }
  }
  const headers = {};
  if (store.unlock) headers['X-Unlock'] = store.unlock;
  const init = {method, cache: 'no-store', headers};
  if (method === 'POST') {
    headers['Content-Type'] = 'application/json';
    headers['X-Token'] = (store.boot && store.boot.csrf) || '';
    init.body = JSON.stringify(body === undefined || body === null ? {} : body);
  }
  const limit = options && options.timeout;
  let timer = 0;
  if (limit) {
    const controller = new AbortController();
    init.signal = controller.signal;
    timer = setTimeout(() => controller.abort(), limit);
  }
  try {
    let response;
    try {
      response = await fetch(path, init);
    } catch (_) {
      throw new ApiError(0, 'network', NO_ANSWER);
    }
    let data = null;
    try {
      data = await response.json();
    } catch (_) {
      if (response.ok) throw new ApiError(response.status, 'bad_reply', NO_ANSWER);
    }
    if (!response.ok) throw fail(response.status, data);
    if (data === null || typeof data !== 'object') throw new ApiError(response.status, 'bad_reply', NO_ANSWER);
    return data;
  } finally {
    clearTimeout(timer);
  }
}

let booting = null;

// Reads /api/bootstrap into store.boot (the page token is part of it). One request at a time.
export function bootstrap(options) {
  if (!booting) {
    booting = send('GET', '/api/bootstrap', undefined, options)
      .then((boot) => { store.apply({boot}); return boot; })
      .finally(() => { booting = null; });
  }
  return booting;
}

function forget(error) {
  //The program says this session is no longer good: the page forgets it, returns to its login panel.
  if (error instanceof ApiError && error.status === 403 && (error.code === 'locked' || error.code === 'pin_unset')) store.setUnlock(null);
}

export async function get(path, options) {
  try {
    return await send('GET', path, undefined, options);
  } catch (error) {
    forget(error);
    throw error;
  }
}

export async function post(path, body, options) {
  let payload = body;
  for (const hook of hooks) {
    const answer = hook('POST', path, payload);
    if (!answer) continue;
    if ('reply' in answer) return answer.reply;
    if ('body' in answer) payload = answer.body;
  }
  try {
    if (!fixture && !(store.boot && store.boot.csrf)) await bootstrap(options);
    try {
      return await send('POST', path, payload, options);
    } catch (error) {
      // The token is new at every start of the program: read it once more instead of failing until a reload.
      if (!(error instanceof ApiError) || error.status !== 403 || error.message !== 'Invalid local request') throw error;
      await bootstrap(options);
      return await send('POST', path, payload, options);
    }
  } catch (error) {
    forget(error);
    throw error;
  }
}
