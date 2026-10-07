import type {
    BulkDeleteResult,
    CurrentUser,
    JobListItem,
    JobStatusResponse,
    ProtocolResult,
    PurificationRequest,
    Trace
} from '../types/api';
import { isRenderableResult } from './resultShape';
import { isRenderableTrace } from './traceShape';

// Production builds are served by the backend itself (APP_ENV=deploy), so they
// call the API on the same origin. An explicitly empty VITE_API_URL does too.
const BASE_URL = import.meta.env.VITE_API_URL ?? (import.meta.env.DEV ? 'http://localhost:8000' : '');

/** Thrown by getResult while the job is still running (the API answers 202). */
export class ResultNotReadyError extends Error {
    constructor(job_id: string) {
        super(`Result for job ${job_id} is not ready yet`);
        this.name = 'ResultNotReadyError';
    }
}

/** Thrown by getResult when the stored report has a shape this version can't render. */
export class OutdatedResultError extends Error {
    constructor(job_id: string) {
        super(`The report for job ${job_id} came from an older version`);
        this.name = 'OutdatedResultError';
    }
}

/** Thrown when the backend has no job with this id (404). Retrying won't help. */
export class JobNotFoundError extends Error {
    constructor(job_id: string) {
        super(`Job ${job_id} was not found`);
        this.name = 'JobNotFoundError';
    }
}

/** Thrown on a 401 (no valid session), after the browser is sent to sign in. */
export class UnauthorizedError extends Error {
    constructor() {
        super('Not signed in');
        this.name = 'UnauthorizedError';
    }
}

/** Where a request without a session sends the browser. Replaceable in tests. */
export const signIn = {
    url: `${BASE_URL}/auth/login`,
    redirect(url: string) {
        window.location.assign(url);
    }
};

// The app's hash routes, e.g. #/report/<id>. The backend checks the same
// shape before redirecting there after sign-in.
const RETURN_ROUTE = /^#\/[A-Za-z0-9/_-]+$/;

/** The sign-in URL, coming back to the current page afterwards when it is one of the app's routes. */
export function signInHere(): string {
    const hash = window.location.hash;
    if (!RETURN_ROUTE.test(hash)) return signIn.url;
    return `${signIn.url}?next=${encodeURIComponent(`/${hash}`)}`;
}

/** Ends the session and Cognito's, then returns to the site. */
export const signOutUrl = `${BASE_URL}/auth/logout`;

/**
 * fetch against the API. A 401 means the session is missing or expired, so
 * the browser goes to sign in and the call throws.
 */
async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
    const url = `${BASE_URL}${path}`;
    const response = await (init ? fetch(url, init) : fetch(url));
    if (response.status === 401) {
        signIn.redirect(signInHere());
        throw new UnauthorizedError();
    }
    return response;
}

/**
 * The API's own explanation of a failed request: FastAPI's `detail`, which is
 * a sentence (429 from the job caps, 409, 400) or a list of validation errors
 * (422). Falls back to the status when the body has neither.
 */
export async function errorMessage(response: Response): Promise<string> {
    try {
        const { detail } = await response.clone().json();
        if (typeof detail === 'string' && detail) return detail;
        if (Array.isArray(detail) && detail.length) {
            return detail
                .map(err => {
                    const field = Array.isArray(err?.loc) ? err.loc.filter((part: unknown) => part !== 'body').join('.') : '';
                    return field ? `${field}: ${err?.msg}` : String(err?.msg ?? err);
                })
                .join('; ');
        }
    } catch {
        // Not JSON; fall through.
    }
    return `API Error: ${response.statusText || response.status}`;
}

async function expectOk(response: Response): Promise<Response> {
    if (!response.ok) {
        throw new Error(await errorMessage(response));
    }
    return response;
}

// --- Client ------------------------------------------------------------------

export const api = {
    async startAnalysis(request: PurificationRequest): Promise<{ job_id: string }> {
        const response = await apiFetch('/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(request)
        });
        return (await expectOk(response)).json();
    },

    async checkStatus(job_id: string): Promise<JobStatusResponse> {
        const response = await apiFetch(`/status/${job_id}`);
        if (response.status === 404) {
            throw new JobNotFoundError(job_id);
        }
        return (await expectOk(response)).json();
    },

    /**
     * The job's report. Throws ResultNotReadyError while it runs (202) and
     * OutdatedResultError when the stored report no longer fits ProtocolResult.
     */
    async getResult(job_id: string): Promise<ProtocolResult> {
        const response = await apiFetch(`/result/${job_id}`);
        // Only 200 carries a report; 202 means the job is still running.
        if (response.status === 202) {
            throw new ResultNotReadyError(job_id);
        }
        if (response.status === 404) {
            throw new JobNotFoundError(job_id);
        }
        if (response.status !== 200) {
            throw new Error(await errorMessage(response));
        }
        const body: unknown = await response.json();
        if (!isRenderableResult(body)) {
            throw new OutdatedResultError(job_id);
        }
        return body;
    },

    /**
     * The job's trace (#77), or null when it has none: it ran before traces
     * existed, is still running, or its stored trace no longer fits.
     */
    async getTrace(job_id: string): Promise<Trace | null> {
        const response = await apiFetch(`/trace/${job_id}`);
        if (response.status === 404) {
            throw new JobNotFoundError(job_id);
        }
        if (response.status !== 200) {
            throw new Error(await errorMessage(response));
        }
        const body: unknown = await response.json();
        return isRenderableTrace(body) ? body : null;
    },

    /**
     * The signed-in user, or null when nobody is signed in (401). Signed-out
     * visitors may browse the home page, so this is the one call that doesn't
     * send them to sign in.
     */
    async me(): Promise<CurrentUser | null> {
        const response = await fetch(`${BASE_URL}/auth/me`);
        if (response.status === 401) return null;
        return (await expectOk(response)).json();
    },

    /** The current user's jobs, newest first. */
    async listJobs(): Promise<JobListItem[]> {
        return (await expectOk(await apiFetch('/jobs'))).json();
    },

    /** Renames one of the user's jobs; a blank name clears it. Returns the updated row. */
    async renameJob(job_id: string, name: string): Promise<JobListItem> {
        const response = await apiFetch(`/jobs/${job_id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name })
        });
        if (response.status === 404) throw new JobNotFoundError(job_id);
        return (await expectOk(response)).json();
    },

    /** Deletes one finished job. Throws if it is running (409) or not the user's (404). */
    async deleteJob(job_id: string): Promise<void> {
        const response = await apiFetch(`/jobs/${job_id}`, { method: 'DELETE' });
        if (response.status === 404) throw new JobNotFoundError(job_id);
        if (response.status === 409) throw new Error("A running job can't be deleted");
        await expectOk(response);
    },

    /** Deletes several jobs; running ones and unknown ones come back as skipped. */
    async deleteJobs(ids: string[]): Promise<BulkDeleteResult> {
        const response = await apiFetch('/jobs/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids })
        });
        return (await expectOk(response)).json();
    }
};
