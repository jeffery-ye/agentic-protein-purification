import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { api, JobNotFoundError, OutdatedResultError, UnauthorizedError } from './client';
import {
    INTERRUPTED_MESSAGE,
    MAX_RETRY_DELAY_MS,
    POLL_INTERVAL_MS,
    retryDelay,
    startPolling,
    stopPolling
} from './poller';
import { jobStore, logStore } from '../stores/job';
import type { JobState, JobStatusResponse, ProgressEntry, ProtocolResult } from '../types/api';

const RESULT: ProtocolResult = { comprehensive_protocol: '## Protocol', blast_results: [] };

function status(state: JobState, messages: string[] = [], extra: Partial<JobStatusResponse> = {}): JobStatusResponse {
    const history: ProgressEntry[] = messages.map((message, i) => ({
        at: `2026-09-23T10:00:0${i}Z`,
        message
    }));
    return { job_id: 'j1', state, progress: messages.at(-1) ?? null, history, ...extra };
}

/** Lets `checkStatus` answer with these responses in order; the last one repeats. */
function statusSequence(...responses: (JobStatusResponse | Error)[]) {
    const spy = vi.spyOn(api, 'checkStatus');
    let i = 0;
    spy.mockImplementation(async () => {
        const next = responses[Math.min(i++, responses.length - 1)];
        if (next instanceof Error) throw next;
        return next;
    });
    return spy;
}

/** Runs the poll loop until the promise settles, and returns how it settled. */
async function settle(promise: Promise<void>) {
    let outcome: { ok: true } | { ok: false; error: unknown } | undefined;
    promise.then(
        () => (outcome = { ok: true }),
        error => (outcome = { ok: false, error })
    );
    for (let i = 0; i < 50 && !outcome; i++) {
        await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    }
    return outcome;
}

beforeEach(() => {
    vi.useFakeTimers();
    jobStore.initiate('j1');
    logStore.clear();
});

afterEach(() => {
    stopPolling();
    vi.useRealTimers();
});

describe('startPolling', () => {
    it('follows a running job to completion and stores its report', async () => {
        const check = statusSequence(
            status('queued'),
            status('running', ['Initializing Agent...']),
            status('completed', ['Initializing Agent...', 'Synthesizing final protocol with LLM...'])
        );
        const result = vi.spyOn(api, 'getResult').mockResolvedValue(RESULT);

        expect(await settle(startPolling('j1'))).toEqual({ ok: true });

        expect(check).toHaveBeenCalledTimes(3);
        expect(result).toHaveBeenCalledWith('j1');
        const view = get(jobStore);
        expect(view.state).toBe('completed');
        expect(view.result).toEqual(RESULT);
    });

    it('checks at once rather than waiting a full interval', async () => {
        const check = statusSequence(status('running'));
        void startPolling('j1');
        await vi.advanceTimersByTimeAsync(0);
        expect(check).toHaveBeenCalledTimes(1);
    });

    it('replays the history into the log once, however many polls repeat it', async () => {
        statusSequence(
            status('running', ['Initializing Agent...', 'Running BLAST...']),
            status('running', ['Initializing Agent...', 'Running BLAST...']),
            status('completed', ['Initializing Agent...', 'Running BLAST...', 'Synthesizing...'])
        );
        vi.spyOn(api, 'getResult').mockResolvedValue(RESULT);

        await settle(startPolling('j1'));

        const logs = get(logStore);
        for (const message of ['Initializing Agent...', 'Running BLAST...', 'Synthesizing...']) {
            expect(logs.filter(line => line.endsWith(message))).toHaveLength(1);
        }
    });

    it('keeps the tracker state in step with each poll', async () => {
        statusSequence(status('running', ['Running BLAST...']));
        void startPolling('j1');
        await vi.advanceTimersByTimeAsync(0);

        const view = get(jobStore);
        expect(view.state).toBe('running');
        expect(view.progress).toBe('Running BLAST...');
        expect(view.history).toHaveLength(1);
    });

    it('rejects with the error when the job fails', async () => {
        statusSequence(status('running'), status('failed', [], { error: 'No BLAST results found' }));

        const outcome = await settle(startPolling('j1'));

        expect(outcome).toMatchObject({ ok: false, error: new Error('No BLAST results found') });
        expect(get(jobStore)).toMatchObject({ state: 'failed', error: 'No BLAST results found' });
    });

    it('marks an interrupted job as interrupted, not failed', async () => {
        statusSequence(status('interrupted'));

        const outcome = await settle(startPolling('j1'));

        expect(outcome?.ok).toBe(false);
        expect(get(jobStore)).toMatchObject({ state: 'interrupted', error: INTERRUPTED_MESSAGE });
    });

    it('retries failed checks instead of failing the job on one dropped request', async () => {
        const check = statusSequence(
            status('running'),
            new Error('Failed to fetch'),
            new Error('Failed to fetch'),
            status('completed')
        );
        vi.spyOn(api, 'getResult').mockResolvedValue(RESULT);

        expect(await settle(startPolling('j1'))).toEqual({ ok: true });
        expect(check).toHaveBeenCalledTimes(4);
        expect(get(logStore).some(line => line.includes('retrying'))).toBe(true);
    });

    it('never fails a running job while the connection is down, and resumes when it is back', async () => {
        // Backing off, eight failed checks span about two and a half minutes.
        const failures = Array.from({ length: 8 }, () => new Error('Failed to fetch'));
        const check = statusSequence(status('running'), ...failures, status('running'), status('completed'));
        vi.spyOn(api, 'getResult').mockResolvedValue(RESULT);
        const polling = startPolling('j1');
        await vi.advanceTimersByTimeAsync(0);

        // Two minutes offline: the job is still running, shown as reconnecting.
        await vi.advanceTimersByTimeAsync(120_000);
        expect(get(jobStore)).toMatchObject({ state: 'running', connectionLost: true, error: null });
        expect(get(logStore).some(line => line.includes('Connection lost'))).toBe(true);

        expect(await settle(polling)).toEqual({ ok: true });
        expect(check).toHaveBeenCalledTimes(11);
        expect(get(jobStore)).toMatchObject({ state: 'completed', result: RESULT, connectionLost: false });
    });

    it('backs off between failed checks, up to a limit', async () => {
        expect(retryDelay(1)).toBe(POLL_INTERVAL_MS);
        expect(retryDelay(2)).toBe(POLL_INTERVAL_MS * 2);
        expect(retryDelay(3)).toBe(POLL_INTERVAL_MS * 4);
        expect(retryDelay(50)).toBe(MAX_RETRY_DELAY_MS);

        const check = statusSequence(new Error('API Error: Bad Gateway'));
        void startPolling('j1');
        await vi.advanceTimersByTimeAsync(0);
        await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 7);
        // At once, then after 2 s, 4 s and 8 s.
        expect(check).toHaveBeenCalledTimes(4);
    });

    it('does not retry after a 401, which is on its way to sign in', async () => {
        const check = statusSequence(new UnauthorizedError());

        const outcome = await settle(startPolling('j1'));

        expect(outcome).toMatchObject({ ok: false });
        expect(check).toHaveBeenCalledTimes(1);
    });

    it('does not retry a job the server does not know', async () => {
        const check = statusSequence(new JobNotFoundError('j1'));

        const outcome = await settle(startPolling('j1'));

        expect(outcome).toMatchObject({ ok: false });
        expect(check).toHaveBeenCalledTimes(1);
    });

    it('shows an outdated report instead of failing', async () => {
        statusSequence(status('completed'));
        vi.spyOn(api, 'getResult').mockRejectedValue(new OutdatedResultError('j1'));

        expect(await settle(startPolling('j1'))).toEqual({ ok: true });
        expect(get(jobStore)).toMatchObject({ state: 'completed', result: null, outdatedResult: true });
    });

    it('does not retry a failed result fetch', async () => {
        statusSequence(status('completed'));
        const result = vi.spyOn(api, 'getResult').mockRejectedValue(new Error('API Error: Bad Request'));

        const outcome = await settle(startPolling('j1'));

        expect(outcome?.ok).toBe(false);
        expect(result).toHaveBeenCalledTimes(1);
        expect(get(jobStore).state).toBe('failed');
    });

    it('runs only one poller at a time', async () => {
        const check = statusSequence(status('running'));
        void startPolling('j1');
        await startPolling('j1');
        await vi.advanceTimersByTimeAsync(0);
        expect(check).toHaveBeenCalledTimes(1);
    });

    it('ignores a check still in flight when polling is stopped', async () => {
        let answer!: (s: JobStatusResponse) => void;
        vi.spyOn(api, 'checkStatus').mockImplementation(
            () => new Promise<JobStatusResponse>(resolve => (answer = resolve))
        );
        void startPolling('j1');
        await vi.advanceTimersByTimeAsync(0);

        stopPolling();
        answer(status('failed', [], { error: 'late' }));
        await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);

        expect(get(jobStore).state).toBe('queued');
    });

    describe('switching jobs while a report is loading', () => {
        // Job A completes and its /result is in flight when the user opens job B.
        async function switchDuringResult(settleA: (answer: { ok: (r: ProtocolResult) => void; fail: (e: Error) => void }) => void) {
            statusSequence(status('completed'));
            let ok!: (r: ProtocolResult) => void;
            let fail!: (e: Error) => void;
            vi.spyOn(api, 'getResult').mockImplementation(
                () => new Promise<ProtocolResult>((resolve, reject) => ((ok = resolve), (fail = reject)))
            );
            void startPolling('j1');
            await vi.advanceTimersByTimeAsync(0);

            // What Processing does when the job in the URL changes.
            stopPolling();
            jobStore.initiate('j2');
            settleA({ ok, fail });
            await vi.advanceTimersByTimeAsync(0);
        }

        it("does not show job A's report as job B's", async () => {
            await switchDuringResult(({ ok }) => ok(RESULT));

            expect(get(jobStore)).toMatchObject({ jobId: 'j2', state: 'queued', result: null });
        });

        it("does not mark job B outdated for job A's report", async () => {
            await switchDuringResult(({ fail }) => fail(new OutdatedResultError('j1')));

            expect(get(jobStore)).toMatchObject({ jobId: 'j2', outdatedResult: false });
        });

        it("does not fail job B when job A's result fetch fails", async () => {
            await switchDuringResult(({ fail }) => fail(new Error('API Error: Bad Gateway')));

            expect(get(jobStore)).toMatchObject({ jobId: 'j2', state: 'queued', error: null });
        });
    });
});
