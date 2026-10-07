import { derived, get, writable } from 'svelte/store';
import { api, UnauthorizedError } from '../api/client';
import type { BulkDeleteResult, JobListItem, JobState } from '../types/api';

// The user's jobs for My Jobs and the header's running-jobs indicator (#25).

/** Refresh this often while a job is queued or running, so progress stays live. */
export const ACTIVE_REFRESH_MS = 3000;
/** And this often otherwise, to pick up jobs started in another tab. */
export const IDLE_REFRESH_MS = 30000;

export function isActive(job: { state: JobState }): boolean {
    return job.state === 'queued' || job.state === 'running';
}

/** The view a job opens in: progress while it runs, otherwise its report (or error). */
export function jobPath(job: { id: string; state: JobState }): string {
    return isActive(job) ? `/processing/${job.id}` : `/report/${job.id}`;
}

/** What a job is called: the name the user gave it, else its input. */
export function jobTitle(job: { name?: string | null; input_summary: string }): string {
    return job.name || job.input_summary;
}

/** 0 -> "0s", 75 -> "1m 15s", 3725 -> "1h 2m". */
export function formatRuntime(seconds: number | null | undefined): string {
    if (seconds == null) return '-';
    const s = Math.round(seconds);
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
    return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

export interface JobsView {
    items: JobListItem[];
    /** True once the first list has arrived. */
    loaded: boolean;
    error: string | null;
}

function createJobsStore() {
    const store = writable<JobsView>({ items: [], loaded: false, error: null });
    const { subscribe, set, update } = store;

    let timer: ReturnType<typeof setTimeout> | null = null;
    let running = false;
    // The timer, a page, a started job and a delete can each start a refresh,
    // and they can answer out of order. Each request and each local change
    // takes the next number, and a response older than the latest is dropped,
    // so a stale list can't bring back a deleted job.
    let latest = 0;

    async function refresh(): Promise<void> {
        const seq = ++latest;
        try {
            const items = await api.listJobs();
            if (seq !== latest) return;
            set({ items, loaded: true, error: null });
        } catch (err) {
            if (seq !== latest || err instanceof UnauthorizedError) return;
            update(view => ({ ...view, error: err instanceof Error ? err.message : 'Could not load jobs' }));
        }
    }

    function schedule() {
        if (!running) return;
        const delay = get(store).items.some(isActive) ? ACTIVE_REFRESH_MS : IDLE_REFRESH_MS;
        timer = setTimeout(async () => {
            timer = null;
            await refresh();
            schedule();
        }, delay);
    }

    function removeLocally(ids: string[]) {
        latest += 1;
        const gone = new Set(ids);
        update(view => ({ ...view, items: view.items.filter(job => !gone.has(job.id)) }));
    }

    return {
        subscribe,
        refresh,

        /** Refreshes now, then keeps refreshing until the returned function is called. */
        startAutoRefresh(): () => void {
            if (!running) {
                running = true;
                void refresh().then(schedule);
            }
            return () => {
                running = false;
                if (timer !== null) clearTimeout(timer);
                timer = null;
            };
        },

        /** A job just started here; show it at once rather than at the next refresh. */
        async jobStarted(): Promise<void> {
            await refresh();
            if (running && timer !== null) {
                clearTimeout(timer);
                timer = null;
                schedule();
            }
        },

        async rename(id: string, name: string): Promise<void> {
            const renamed = await api.renameJob(id, name);
            latest += 1;
            update(view => ({ ...view, items: view.items.map(job => (job.id === id ? renamed : job)) }));
        },

        async deleteOne(id: string): Promise<void> {
            await api.deleteJob(id);
            removeLocally([id]);
        },

        async deleteMany(ids: string[]): Promise<BulkDeleteResult> {
            const result = await api.deleteJobs(ids);
            removeLocally(result.deleted);
            return result;
        },

        reset() {
            latest += 1;
            set({ items: [], loaded: false, error: null });
        }
    };
}

export const jobsStore = createJobsStore();

/** The user's queued and running jobs, oldest first. */
export const activeJobs = derived(jobsStore, view =>
    view.items.filter(isActive).sort((a, b) => a.created_at.localeCompare(b.created_at))
);
