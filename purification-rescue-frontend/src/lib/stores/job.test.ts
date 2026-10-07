import { beforeEach, describe, expect, it } from 'vitest';
import { get } from 'svelte/store';
import { jobStore, logStore } from './job';

beforeEach(() => {
    jobStore.reset();
    logStore.clear();
});

describe('jobStore', () => {
    it('starts a job queued, with nothing left over from the previous one', () => {
        jobStore.initiate('old');
        jobStore.fail('boom');
        jobStore.initiate('new');

        expect(get(jobStore)).toMatchObject({
            jobId: 'new',
            state: 'queued',
            history: [],
            result: null,
            outdatedResult: false,
            error: null
        });
    });

    it('takes state, progress, history and inputs from a status response', () => {
        jobStore.initiate('j1');
        jobStore.setStatus({
            job_id: 'j1',
            state: 'running',
            progress: 'Running BLAST...',
            history: [{ at: '2026-09-23T10:00:00Z', message: 'Running BLAST...' }],
            inputs: { fasta_id: 'P12345' }
        });

        expect(get(jobStore)).toMatchObject({
            state: 'running',
            progress: 'Running BLAST...',
            history: [{ message: 'Running BLAST...' }],
            inputs: { fasta_id: 'P12345' }
        });
    });

    it('keeps known inputs when a status response omits them', () => {
        jobStore.initiate('j1');
        jobStore.setStatus({ job_id: 'j1', state: 'queued', inputs: { fasta_id: 'P12345' } });
        jobStore.setStatus({ job_id: 'j1', state: 'running' });

        expect(get(jobStore).inputs).toEqual({ fasta_id: 'P12345' });
    });

    it("drops the last job's report when a status for another job arrives", () => {
        jobStore.initiate('a');
        jobStore.complete({ comprehensive_protocol: 'A' });
        jobStore.setStatus({ job_id: 'b', state: 'completed' });

        expect(get(jobStore)).toMatchObject({ jobId: 'b', state: 'completed', result: null, outdatedResult: false });
    });

    it('clears the connection-lost notice on the next status', () => {
        jobStore.initiate('j1');
        jobStore.setConnectionLost(true);
        expect(get(jobStore).connectionLost).toBe(true);

        jobStore.setStatus({ job_id: 'j1', state: 'running' });
        expect(get(jobStore).connectionLost).toBe(false);
    });

    it('records a completed report', () => {
        jobStore.initiate('j1');
        jobStore.complete({ comprehensive_protocol: 'x' });

        expect(get(jobStore)).toMatchObject({ state: 'completed', result: { comprehensive_protocol: 'x' } });
    });

    it('marks a completed job whose report is outdated', () => {
        jobStore.initiate('j1');
        jobStore.markOutdated();

        expect(get(jobStore)).toMatchObject({ state: 'completed', result: null, outdatedResult: true });
    });

    it('fails as failed by default, or as interrupted', () => {
        jobStore.fail('boom');
        expect(get(jobStore)).toMatchObject({ state: 'failed', error: 'boom' });

        jobStore.fail('restarted', 'interrupted');
        expect(get(jobStore)).toMatchObject({ state: 'interrupted', error: 'restarted' });
    });
});

describe('logStore', () => {
    const history = [
        { at: '2026-09-23T10:00:00Z', message: 'Initializing Agent...' },
        { at: '2026-09-23T10:00:05Z', message: 'Running BLAST...' }
    ];

    it('adds only the history entries it has not shown yet', () => {
        logStore.syncHistory(history.slice(0, 1));
        logStore.syncHistory(history);
        logStore.syncHistory(history);

        const logs = get(logStore);
        expect(logs).toHaveLength(2);
        expect(logs[0]).toMatch(/Initializing Agent\.\.\.$/);
        expect(logs[1]).toMatch(/Running BLAST\.\.\.$/);
    });

    it('keeps a repeated message that was recorded at a different time', () => {
        logStore.syncHistory([
            { at: '2026-09-23T10:00:00Z', message: 'Processing' },
            { at: '2026-09-23T10:00:01Z', message: 'Processing' }
        ]);

        expect(get(logStore)).toHaveLength(2);
    });

    it('replays the history again after being cleared', () => {
        logStore.syncHistory(history);
        logStore.clear();
        logStore.syncHistory(history);

        expect(get(logStore)).toHaveLength(2);
    });

    it('does not repeat the same client message twice in a row', () => {
        logStore.addLog('Connecting to the job...');
        logStore.addLog('Connecting to the job...');

        expect(get(logStore)).toHaveLength(1);
    });

    it('does not crash on a malformed timestamp', () => {
        logStore.syncHistory([{ at: 'not a date', message: 'Hello' }]);

        expect(get(logStore)[0]).toBe('[--:--:--] Hello');
    });
});
