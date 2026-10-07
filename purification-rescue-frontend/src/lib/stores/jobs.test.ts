import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { api } from '../api/client';
import {
    ACTIVE_REFRESH_MS,
    IDLE_REFRESH_MS,
    activeJobs,
    formatRuntime,
    jobPath,
    jobsStore,
    jobTitle
} from './jobs';
import type { JobListItem, JobState } from '../types/api';

function job(id: string, state: JobState, created_at = '2026-09-23T10:00:00Z'): JobListItem {
    return { id, input_summary: id, state, created_at };
}

beforeEach(() => {
    jobsStore.reset();
});

describe('helpers', () => {
    it.each([
        [null, '-'],
        [0, '0s'],
        [59.4, '59s'],
        [75, '1m 15s'],
        [3725, '1h 2m']
    ])('formatRuntime(%s) is %s', (seconds, text) => {
        expect(formatRuntime(seconds)).toBe(text);
    });

    it('opens a running job in its progress view, and anything else as its report', () => {
        expect(jobPath(job('a', 'queued'))).toBe('/processing/a');
        expect(jobPath(job('a', 'running'))).toBe('/processing/a');
        expect(jobPath(job('a', 'completed'))).toBe('/report/a');
        expect(jobPath(job('a', 'failed'))).toBe('/report/a');
        expect(jobPath(job('a', 'interrupted'))).toBe('/report/a');
    });

    it('titles a job by its name, else its input', () => {
        expect(jobTitle({ name: 'Kinase', input_summary: 'P9WQA3' })).toBe('Kinase');
        expect(jobTitle({ name: null, input_summary: 'P9WQA3' })).toBe('P9WQA3');
    });
});

describe('jobsStore', () => {
    it('loads the list and marks it loaded', async () => {
        vi.spyOn(api, 'listJobs').mockResolvedValue([job('a', 'completed')]);

        await jobsStore.refresh();

        expect(get(jobsStore)).toMatchObject({ loaded: true, error: null, items: [{ id: 'a' }] });
    });

    it('keeps the last list and reports the error when a refresh fails', async () => {
        const list = vi.spyOn(api, 'listJobs').mockResolvedValue([job('a', 'completed')]);
        await jobsStore.refresh();
        list.mockRejectedValue(new Error('API Error: 500'));

        await jobsStore.refresh();

        expect(get(jobsStore)).toMatchObject({ error: 'API Error: 500', items: [{ id: 'a' }] });
    });

    it('lists running and queued jobs as active, oldest first', async () => {
        vi.spyOn(api, 'listJobs').mockResolvedValue([
            job('new', 'running', '2026-09-23T12:00:00Z'),
            job('done', 'completed', '2026-09-23T11:00:00Z'),
            job('old', 'queued', '2026-09-23T10:00:00Z')
        ]);

        await jobsStore.refresh();

        expect(get(activeJobs).map(j => j.id)).toEqual(['old', 'new']);
    });

    it('ignores a refresh that answers after a newer one', async () => {
        const answers: ((items: JobListItem[]) => void)[] = [];
        vi.spyOn(api, 'listJobs').mockImplementation(() => new Promise(resolve => answers.push(resolve)));

        const older = jobsStore.refresh();
        const newer = jobsStore.refresh();
        answers[1]([job('b', 'completed')]);
        await newer;
        answers[0]([job('a', 'completed'), job('b', 'completed')]);
        await older;

        expect(get(jobsStore).items.map(j => j.id)).toEqual(['b']);
    });

    it('does not bring back a deleted job from a refresh started before the delete', async () => {
        let answer!: (items: JobListItem[]) => void;
        vi.spyOn(api, 'listJobs').mockImplementation(() => new Promise(resolve => (answer = resolve)));
        vi.spyOn(api, 'deleteJob').mockResolvedValue();

        const stale = jobsStore.refresh();
        await jobsStore.deleteOne('a');
        answer([job('a', 'completed'), job('b', 'failed')]);
        await stale;

        expect(get(jobsStore).items.map(j => j.id)).not.toContain('a');
    });

    it('drops a deleted job at once', async () => {
        vi.spyOn(api, 'listJobs').mockResolvedValue([job('a', 'completed'), job('b', 'failed')]);
        vi.spyOn(api, 'deleteJob').mockResolvedValue();
        await jobsStore.refresh();

        await jobsStore.deleteOne('a');

        expect(get(jobsStore).items.map(j => j.id)).toEqual(['b']);
    });

    it('shows a renamed job at once', async () => {
        vi.spyOn(api, 'listJobs').mockResolvedValue([job('a', 'completed'), job('b', 'failed')]);
        vi.spyOn(api, 'renameJob').mockResolvedValue({ ...job('a', 'completed'), name: 'Kinase' });
        await jobsStore.refresh();

        await jobsStore.rename('a', 'Kinase');

        expect(api.renameJob).toHaveBeenCalledWith('a', 'Kinase');
        expect(get(jobsStore).items.map(j => j.name ?? null)).toEqual(['Kinase', null]);
    });

    it('drops only the jobs a bulk delete actually deleted', async () => {
        vi.spyOn(api, 'listJobs').mockResolvedValue([job('a', 'completed'), job('b', 'running')]);
        vi.spyOn(api, 'deleteJobs').mockResolvedValue({ deleted: ['a'], skipped: [{ id: 'b', reason: 'running' }] });
        await jobsStore.refresh();

        const result = await jobsStore.deleteMany(['a', 'b']);

        expect(result.skipped).toEqual([{ id: 'b', reason: 'running' }]);
        expect(get(jobsStore).items.map(j => j.id)).toEqual(['b']);
    });
});

describe('jobsStore.startAutoRefresh', () => {
    let stop: (() => void) | undefined;

    beforeEach(() => {
        vi.useFakeTimers();
    });

    afterEach(() => {
        stop?.();
        vi.useRealTimers();
    });

    it('refreshes quickly while a job runs, and slowly once none does', async () => {
        const list = vi.spyOn(api, 'listJobs').mockResolvedValue([job('a', 'running')]);

        stop = jobsStore.startAutoRefresh();
        await vi.advanceTimersByTimeAsync(0);
        expect(list).toHaveBeenCalledTimes(1);

        await vi.advanceTimersByTimeAsync(ACTIVE_REFRESH_MS);
        expect(list).toHaveBeenCalledTimes(2);

        list.mockResolvedValue([job('a', 'completed')]);
        await vi.advanceTimersByTimeAsync(ACTIVE_REFRESH_MS);
        expect(list).toHaveBeenCalledTimes(3);

        await vi.advanceTimersByTimeAsync(ACTIVE_REFRESH_MS);
        expect(list).toHaveBeenCalledTimes(3);
        await vi.advanceTimersByTimeAsync(IDLE_REFRESH_MS);
        expect(list).toHaveBeenCalledTimes(4);
    });

    it('stops refreshing when stopped', async () => {
        const list = vi.spyOn(api, 'listJobs').mockResolvedValue([job('a', 'running')]);

        stop = jobsStore.startAutoRefresh();
        await vi.advanceTimersByTimeAsync(0);
        stop();
        await vi.advanceTimersByTimeAsync(IDLE_REFRESH_MS * 2);

        expect(list).toHaveBeenCalledTimes(1);
    });

    it('shows a newly started job at once and switches to the quick refresh', async () => {
        const list = vi.spyOn(api, 'listJobs').mockResolvedValue([]);
        stop = jobsStore.startAutoRefresh();
        await vi.advanceTimersByTimeAsync(0);

        list.mockResolvedValue([job('a', 'queued')]);
        await jobsStore.jobStarted();
        expect(get(activeJobs).map(j => j.id)).toEqual(['a']);

        await vi.advanceTimersByTimeAsync(ACTIVE_REFRESH_MS);
        expect(list).toHaveBeenCalledTimes(3);
    });
});
