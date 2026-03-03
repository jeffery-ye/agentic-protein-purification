import { writable } from 'svelte/store';
import type { JobStatus, ProtocolResult } from '../types/api';

// --- Job Store ---

export interface JobState {
    jobId: string | null;
    status: JobStatus;
    result: ProtocolResult | null;
    error: string | null;
}

const initialJobState: JobState = {
    jobId: null,
    status: 'PENDING',
    result: null,
    error: null
};

function createJobStore() {
    const { subscribe, set, update } = writable<JobState>(initialJobState);

    return {
        subscribe,
        initiate: (id: string) => {
            set({
                jobId: id,
                status: 'PENDING',
                result: null,
                error: null
            });
        },
        setStatus: (status: JobStatus) => {
            update(state => ({ ...state, status }));
        },
        complete: (data: ProtocolResult) => {
            update(state => ({
                ...state,
                status: 'COMPLETED',
                result: data,
                error: null
            }));
        },
        fail: (errorMsg: string) => {
            update(state => ({
                ...state,
                status: 'ERROR',
                result: null,
                error: errorMsg
            }));
        },
        reset: () => set(initialJobState)
    };
}

export const jobStore = createJobStore();

// --- Log Store ---

function createLogStore() {
    const { subscribe, update, set } = writable<string[]>([]);

    return {
        subscribe,
        addLog: (message: string) => {
            const timestamp = new Date().toLocaleTimeString([], { hour12: false });
            const logEntry = `[${timestamp}] ${message}`;

            update(logs => {
                const lastLog = logs[logs.length - 1];
                if (lastLog && lastLog.endsWith(message)) {
                    return logs;
                }
                return [...logs, logEntry];
            });
        },
        clear: () => set([])
    };
}

export const logStore = createLogStore();
