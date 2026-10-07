import { get } from 'svelte/store';
import { api, JobNotFoundError, OutdatedResultError, UnauthorizedError } from './client';
import { jobStore, logStore } from '../stores/job';
import type { JobStatusResponse } from '../types/api';

export const POLL_INTERVAL_MS = 2000;

// A job runs for minutes, and a dropped connection or a deploy restart
// shouldn't end it. Failed checks back off to at most this, and never give up.
export const MAX_RETRY_DELAY_MS = 30000;

export const INTERRUPTED_MESSAGE =
    'The server restarted while this job was running, so it stopped before finishing. Please submit it again.';

let timer: ReturnType<typeof setTimeout> | null = null;
let isPolling = false;
// Bumped by stopPolling, so a check still in flight from an earlier run is ignored.
let generation = 0;

export function stopPolling() {
    if (timer !== null) {
        clearTimeout(timer);
        timer = null;
    }
    isPolling = false;
    generation += 1;
}

function messageOf(err: unknown, fallback: string): string {
    return err instanceof Error && err.message ? err.message : fallback;
}

/** The wait before the next check after this many failed ones in a row. */
export function retryDelay(failures: number): number {
    return Math.min(POLL_INTERVAL_MS * 2 ** Math.max(failures - 1, 0), MAX_RETRY_DELAY_MS);
}

/**
 * Polls `/status` until the job leaves `queued`/`running`: checks at once, then
 * every POLL_INTERVAL_MS, one check at a time. Each response's full history is
 * synced into the log, so opening the view mid-run replays it. Resolves once a
 * completed job's report is in the job store (or marked outdated); rejects when
 * the job failed or was interrupted, or is unknown. Network and server errors
 * are retried with backoff for as long as the view stays open. Only one poller
 * runs, and nothing from a stopped one reaches the store.
 */
export function startPolling(jobId: string): Promise<void> {
    if (isPolling) return Promise.resolve();
    isPolling = true;
    const run = generation;
    // Still this run, and the store still shows this job.
    const active = () => isPolling && generation === run && get(jobStore).jobId === jobId;

    logStore.addLog('Connecting to the job...');
    let failures = 0;

    return new Promise((resolve, reject) => {
        const schedule = (delay = POLL_INTERVAL_MS) => {
            timer = setTimeout(tick, delay);
        };

        const giveUp = (err: unknown, msg: string) => {
            stopPolling();
            logStore.addLog(`Fatal Error: ${msg}`);
            jobStore.fail(msg);
            reject(err);
        };

        // Polling stays on while the result loads, so switching jobs meanwhile
        // (which stops it) drops the result rather than showing it as the new job's.
        const fetchResult = async () => {
            logStore.addLog('Analysis completed. Fetching results...');
            try {
                const result = await api.getResult(jobId);
                if (!active()) return;
                stopPolling();
                jobStore.complete(result);
                resolve();
            } catch (err) {
                if (!active()) return;
                if (err instanceof OutdatedResultError) {
                    stopPolling();
                    logStore.addLog('The report came from an older version and cannot be shown in full.');
                    jobStore.markOutdated();
                    resolve();
                    return;
                }
                // The job is finished, so a failed result fetch isn't retried.
                giveUp(err, messageOf(err, 'Could not fetch the result'));
            }
        };

        const tick = async () => {
            timer = null;
            let status: JobStatusResponse;
            try {
                status = await api.checkStatus(jobId);
            } catch (err) {
                if (!active()) return;
                const msg = messageOf(err, 'Network error during polling');
                // An unknown job stays unknown, and a 401 is already on its way
                // to sign in. Anything else is the network or the server.
                if (err instanceof JobNotFoundError || err instanceof UnauthorizedError) {
                    giveUp(err, msg);
                    return;
                }
                failures += 1;
                jobStore.setConnectionLost(true);
                logStore.addLog(`Connection lost (${msg}); retrying...`);
                schedule(retryDelay(failures));
                return;
            }
            if (!active()) return;
            if (failures > 0) logStore.addLog('Connection restored.');
            failures = 0;

            jobStore.setStatus(status);
            logStore.syncHistory(status.history ?? []);

            switch (status.state) {
                case 'completed':
                    await fetchResult();
                    return;
                case 'failed': {
                    stopPolling();
                    const msg = status.error || 'Unknown error occurred during analysis';
                    logStore.addLog(`Error: ${msg}`);
                    jobStore.fail(msg, 'failed');
                    reject(new Error(msg));
                    return;
                }
                case 'interrupted': {
                    stopPolling();
                    const msg = status.error || INTERRUPTED_MESSAGE;
                    logStore.addLog(`Interrupted: ${msg}`);
                    jobStore.fail(msg, 'interrupted');
                    reject(new Error(msg));
                    return;
                }
                default:
                    // queued or running
                    schedule();
            }
        };

        void tick();
    });
}
