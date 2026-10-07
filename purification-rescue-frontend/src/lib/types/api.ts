import type { components } from './openapi';

// Input bounds, the same as schemas.py (#26).
export const MAX_FASTA_LENGTH = 20_000;
export const MAX_FAILED_PURIFICATION_LENGTH = 20_000;
/** Source protocols to extract before the literature search stops. */
export const MAX_PROTOCOLS = 5;
export const DEFAULT_PROTOCOLS = 3;
/** The longest name a job can be given in My Jobs. */
export const MAX_JOB_NAME_LENGTH = 200;

// Wire types for the endpoints with a response model are generated from the
// backend's OpenAPI schema (#46): run `uv run python scripts/export_openapi.py`,
// then `npm run gen:api`. The rest are kept by hand below.
type Schemas = components['schemas'];

export type PurificationRequest = Schemas['PurificationRequest'];
export type BufferStep = Schemas['BufferStep'];
/** A source protocol: a paper's, or the failed one. */
export type Purification = Schemas['SourceProtocol'];
export type BlastResult = Schemas['Hit'];
export type HitStatus = Schemas['HitStatus'];
export type Paper = Schemas['Paper'];
export type ProtocolResult = Schemas['ProtocolResult'];
/** A job's trace (#77): its LLM calls and stage times. */
export type Trace = Schemas['Trace'];
export type TraceStep = Schemas['TraceStep'];

/** A job's lifecycle state, as `/status` reports it. */
export type JobState = Schemas['JobListItem']['state'];

/** One progress message and when the backend recorded it (ISO 8601, UTC). */
export interface ProgressEntry {
    at: string;
    message: string;
}

/** The `/status/{job_id}` response. */
export interface JobStatusResponse {
    job_id: string;
    state: JobState;
    /** The latest progress message; null until the job reports one. */
    progress?: string | null;
    /** Every progress message so far, oldest first. */
    history?: ProgressEntry[] | null;
    inputs?: PurificationRequest | null;
    created_at?: string | null;
    started_at?: string | null;
    finished_at?: string | null;
    /** Set when the job failed or was interrupted. */
    error?: string | null;
}

/** The `/auth/me` response: the signed-in user. */
export interface CurrentUser {
    sub: string;
    username: string;
    email?: string | null;
    /** False in dev and before Cognito is switched on; there is no sign-out then. */
    auth_enabled: boolean;
    /** False for the shared reviewer account and without Cognito. */
    can_change_password: boolean;
    change_password_url?: string | null;
}

/** One row of `GET /jobs` (My Jobs). */
export type JobListItem = Schemas['JobListItem'];

/** The `POST /jobs/delete` response. */
export type BulkDeleteResult = Schemas['BulkDeleteResult'];
