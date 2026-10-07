import { writable } from 'svelte/store';
import type {
    JobState,
    JobStatusResponse,
    ProgressEntry,
    ProtocolResult,
    PurificationRequest
} from '../types/api';

// --- Job Store ---

export interface JobView {
    jobId: string | null;
    state: JobState;
    /** The latest progress message. */
    progress: string | null;
    /** Every progress message so far, oldest first. */
    history: ProgressEntry[];
    inputs: PurificationRequest | null;
    result: ProtocolResult | null;
    /** The job completed, but its stored report has a shape this version can't render. */
    outdatedResult: boolean;
    error: string | null;
    /** Recent status checks failed with a network or server error; polling keeps retrying. */
    connectionLost: boolean;
}

const initialJobView: JobView = {
    jobId: null,
    state: 'queued',
    progress: null,
    history: [],
    inputs: null,
    result: null,
    outdatedResult: false,
    error: null,
    connectionLost: false
};

function createJobStore() {
    const { subscribe, set, update } = writable<JobView>(initialJobView);

    return {
        subscribe,
        initiate: (id: string) => {
            set({ ...initialJobView, jobId: id });
        },
        /**
         * Takes a `/status` response. One for a different job starts from a
         * clean view, so no report or error carries over from the last job.
         */
        setStatus: (status: JobStatusResponse) => {
            update(current => {
                const view = current.jobId === status.job_id ? current : { ...initialJobView, jobId: status.job_id };
                return {
                    ...view,
                    state: status.state,
                    progress: status.progress ?? null,
                    history: status.history ?? [],
                    inputs: status.inputs ?? view.inputs,
                    error: status.error ?? view.error,
                    connectionLost: false
                };
            });
        },
        /** Status checks started failing, or recovered, while polling retries. */
        setConnectionLost: (lost: boolean) => {
            update(view => (view.connectionLost === lost ? view : { ...view, connectionLost: lost }));
        },
        complete: (data: ProtocolResult) => {
            update(view => ({
                ...view,
                state: 'completed',
                result: data,
                outdatedResult: false,
                error: null
            }));
        },
        markOutdated: () => {
            update(view => ({
                ...view,
                state: 'completed',
                result: null,
                outdatedResult: true
            }));
        },
        fail: (errorMsg: string, state: 'failed' | 'interrupted' = 'failed') => {
            update(view => ({
                ...view,
                state,
                result: null,
                error: errorMsg,
                connectionLost: false
            }));
        },
        reset: () => set(initialJobView)
    };
}

export const jobStore = createJobStore();

// --- Log Store ---

function timeOf(date: Date): string {
    if (Number.isNaN(date.getTime())) return '--:--:--';
    return date.toLocaleTimeString([], { hour12: false });
}

function createLogStore() {
    const { subscribe, update, set } = writable<string[]>([]);
    // Progress entries already shown, so each poll's full history adds only new ones.
    let seen = new Set<string>();

    return {
        subscribe,
        /** A message from the client itself (not from the job's history). */
        addLog: (message: string) => {
            const logEntry = `[${timeOf(new Date())}] ${message}`;

            update(logs => {
                const lastLog = logs[logs.length - 1];
                if (lastLog && lastLog.endsWith(message)) {
                    return logs;
                }
                return [...logs, logEntry];
            });
        },
        /**
         * Appends the entries of a job's progress history that aren't shown yet.
         * Called with the full history on every poll, so opening the view mid-run
         * replays everything so far and later polls add only what's new.
         */
        syncHistory: (history: ProgressEntry[]) => {
            const fresh = history.filter(entry => !seen.has(`${entry.at}|${entry.message}`));
            if (fresh.length === 0) return;
            for (const entry of fresh) seen.add(`${entry.at}|${entry.message}`);
            update(logs => [
                ...logs,
                ...fresh.map(entry => `[${timeOf(new Date(entry.at))}] ${entry.message}`)
            ]);
        },
        clear: () => {
            seen = new Set();
            set([]);
        }
    };
}

export const logStore = createLogStore();
