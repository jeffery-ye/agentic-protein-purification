import { afterEach, describe, expect, it, vi } from 'vitest';
import {
    api,
    JobNotFoundError,
    OutdatedResultError,
    ResultNotReadyError,
    signIn,
    UnauthorizedError
} from './client';
import type { JobStatusResponse, ProtocolResult } from '../types/api';

const RESULT: ProtocolResult = {
    comprehensive_protocol: '## Protocol',
    purifications: [{ article_link: 'https://example.org', protocol: [{ purification_step: 'Lysis' }] }],
    blast_results: [
        {
            protein_name: 'Hit',
            pdb_id: '1ABC',
            length: 100,
            pident: 95,
            e_value: 0,
            query_coverage: 100,
            query_start: 1,
            query_end: 100,
            subject_start: 1,
            subject_end: 100,
            similarity_score: 0.9
        }
    ],
    error_message: null
};

function respond(status: number, body: unknown = {}) {
    const fetchMock = vi.fn().mockResolvedValue(
        new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
    );
    vi.stubGlobal('fetch', fetchMock);
    return fetchMock;
}

afterEach(() => {
    vi.unstubAllGlobals();
});

describe('checkStatus', () => {
    it('returns the status body', async () => {
        const status: JobStatusResponse = {
            job_id: 'j1',
            state: 'running',
            progress: 'Running BLAST...',
            history: [{ at: '2026-09-23T10:00:00Z', message: 'Running BLAST...' }]
        };
        const fetchMock = respond(200, status);

        await expect(api.checkStatus('j1')).resolves.toEqual(status);
        expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/status\/j1$/));
    });

    it('throws JobNotFoundError on 404', async () => {
        respond(404);
        await expect(api.checkStatus('gone')).rejects.toBeInstanceOf(JobNotFoundError);
    });

    it('throws a plain error on other failures', async () => {
        respond(500);
        const err = await api.checkStatus('j1').catch(e => e);
        expect(err).toBeInstanceOf(Error);
        expect(err).not.toBeInstanceOf(JobNotFoundError);
    });
});

describe('without a session (401)', () => {
    it.each([
        ['checkStatus', () => api.checkStatus('j1')],
        ['getResult', () => api.getResult('j1')],
        ['listJobs', () => api.listJobs()],
        ['deleteJob', () => api.deleteJob('j1')],
        ['deleteJobs', () => api.deleteJobs(['j1'])],
        ['startAnalysis', () => api.startAnalysis({ fasta_id: 'P12345' })]
    ])('%s sends the browser to sign in and throws', async (_name, call) => {
        respond(401, { detail: 'Not signed in' });
        const redirect = vi.spyOn(signIn, 'redirect').mockImplementation(() => {});

        await expect(call()).rejects.toBeInstanceOf(UnauthorizedError);
        expect(redirect).toHaveBeenCalledWith(expect.stringMatching(/\/auth\/login$/));
    });

    it('me returns null for a signed-out visitor instead of redirecting', async () => {
        respond(401, { detail: 'Not signed in' });
        const redirect = vi.spyOn(signIn, 'redirect').mockImplementation(() => {});

        await expect(api.me()).resolves.toBeNull();
        expect(redirect).not.toHaveBeenCalled();
    });

    it('does not redirect on other errors', async () => {
        respond(500);
        const redirect = vi.spyOn(signIn, 'redirect').mockImplementation(() => {});

        await expect(api.listJobs()).rejects.toThrow();
        expect(redirect).not.toHaveBeenCalled();
    });
});

describe('error messages', () => {
    it('shows the job cap message from a 429', async () => {
        respond(429, { detail: 'The daily job limit has been reached. Please try again tomorrow.' });
        await expect(api.startAnalysis({ fasta_id: 'P12345' })).rejects.toThrow(
            'The daily job limit has been reached. Please try again tomorrow.'
        );
    });

    it('lists validation errors from a 422 with their fields', async () => {
        respond(422, {
            detail: [
                { loc: ['body', 'max_hits'], msg: 'Input should be less than or equal to 50' },
                { loc: ['body', 'fasta_id'], msg: 'String should have at most 20000 characters' }
            ]
        });
        await expect(api.startAnalysis({ fasta_id: 'P12345' })).rejects.toThrow(
            'max_hits: Input should be less than or equal to 50; fasta_id: String should have at most 20000 characters'
        );
    });

    it('falls back to the status when the body has no detail', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('oops', { status: 502, statusText: 'Bad Gateway' })));
        await expect(api.listJobs()).rejects.toThrow('API Error: Bad Gateway');
    });
});

describe('jobs', () => {
    it('lists the user\'s jobs', async () => {
        const jobs = [{ id: 'j1', input_summary: 'P12345', state: 'completed', created_at: '2026-09-23T10:00:00Z' }];
        const fetchMock = respond(200, jobs);

        await expect(api.listJobs()).resolves.toEqual(jobs);
        expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/jobs$/));
    });

    it('deletes one job with DELETE', async () => {
        const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
        vi.stubGlobal('fetch', fetchMock);

        await api.deleteJob('j1');
        expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/jobs\/j1$/), { method: 'DELETE' });
    });

    it('refuses to delete a running job (409)', async () => {
        respond(409, { detail: "A running job can't be deleted" });
        await expect(api.deleteJob('j1')).rejects.toThrow(/running/);
    });

    it('treats another user\'s job as not found (404)', async () => {
        respond(404);
        await expect(api.deleteJob('j1')).rejects.toBeInstanceOf(JobNotFoundError);
    });

    it('deletes several jobs in one request and returns what was skipped', async () => {
        const result = { deleted: ['a'], skipped: [{ id: 'b', reason: 'running' }] };
        const fetchMock = respond(200, result);

        await expect(api.deleteJobs(['a', 'b'])).resolves.toEqual(result);
        const [url, init] = fetchMock.mock.calls[0];
        expect(url).toMatch(/\/jobs\/delete$/);
        expect(init).toMatchObject({ method: 'POST', body: JSON.stringify({ ids: ['a', 'b'] }) });
    });
});

describe('getResult', () => {
    it('returns a renderable report', async () => {
        respond(200, RESULT);
        await expect(api.getResult('j1')).resolves.toEqual(RESULT);
    });

    it('treats 202 as not ready rather than as a blank report', async () => {
        respond(202, { detail: 'running' });
        await expect(api.getResult('j1')).rejects.toBeInstanceOf(ResultNotReadyError);
    });

    it('throws JobNotFoundError on 404', async () => {
        respond(404);
        await expect(api.getResult('gone')).rejects.toBeInstanceOf(JobNotFoundError);
    });

    it('throws a plain error on a failed job (400)', async () => {
        respond(400, { detail: 'Job failed' });
        const err = await api.getResult('j1').catch(e => e);
        expect(err).toBeInstanceOf(Error);
        expect(err).not.toBeInstanceOf(ResultNotReadyError);
        expect(err).not.toBeInstanceOf(OutdatedResultError);
    });

    it('throws OutdatedResultError when the stored report no longer fits', async () => {
        respond(200, { ...RESULT, blast_results: [{ pdb_id: '1ABC', pident: '95%' }] });
        await expect(api.getResult('old')).rejects.toBeInstanceOf(OutdatedResultError);
    });
});
