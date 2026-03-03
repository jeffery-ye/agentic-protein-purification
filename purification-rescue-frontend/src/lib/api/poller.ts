import { api } from './client';
import { jobStore, logStore } from '../stores/job';
import type { JobStatus } from '../types/api';

let pollingInterval: number | null = null;
let isPolling = false;

export function stopPolling() {
    if (pollingInterval) {
        clearInterval(pollingInterval);
        pollingInterval = null;
    }
    isPolling = false;
}

export async function startPolling(jobId: string): Promise<void> {
    // Prevent duplicate pollers
    if (isPolling) return;
    isPolling = true;

    // Immediate initial check
    logStore.addLog('Initializing polling...');

    return new Promise((resolve, reject) => {
        pollingInterval = window.setInterval(async () => {
            try {
                const { status, error } = await api.checkStatus(jobId);

                jobStore.setStatus(status);

                if (status === 'COMPLETED') {
                    stopPolling();
                    logStore.addLog('Analysis completed. Fetching results...');
                    const result = await api.getResult(jobId);
                    jobStore.complete(result);
                    resolve();
                } else if (status === 'ERROR') {
                    stopPolling();
                    const errorMsg = error || 'Unknown error occurred during analysis';
                    logStore.addLog(`Error: ${errorMsg}`);
                    jobStore.fail(errorMsg);
                    reject(new Error(errorMsg));
                } else {
                    // Still running or initializing, log status if interesting
                    logStore.addLog(`Status: ${status}`);
                }

            } catch (err: any) {
                stopPolling();
                const msg = err.message || 'Network error during polling';
                logStore.addLog(`Fatal Error: ${msg}`);
                jobStore.fail(msg);
                reject(err);
            }
        }, 2000);
    });
}
