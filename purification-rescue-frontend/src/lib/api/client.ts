import type { PurificationRequest, ProtocolResult, JobStatus } from '../types/api';
import { MOCK_SUCCESS_RESULT } from './mockData';

const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true';

// Mock state helper
const mockJobStates: Record<string, { start: number, status: JobStatus }> = {};

export const api = {
    async startAnalysis(request: PurificationRequest): Promise<{ job_id: string }> {
        if (USE_MOCK) {
            const job_id = crypto.randomUUID();
            mockJobStates[job_id] = { start: Date.now(), status: 'PENDING' };
            console.log(`[Mock API] Started analysis job: ${job_id}`);
            return { job_id };
        }

        const response = await fetch(`${BASE_URL}/analyze`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(request)
        });

        if (!response.ok) {
            throw new Error(`API Error: ${response.statusText}`);
        }

        return response.json();
    },

    async checkStatus(job_id: string): Promise<{ status: JobStatus; error?: string }> {
        if (USE_MOCK) {
            const job = mockJobStates[job_id];
            if (!job) return { status: 'ERROR', error: 'Job not found' };

            const elapsed = Date.now() - job.start;
            let status: JobStatus = 'PENDING';

            if (elapsed < 2000) {
                status = 'Initializing';
            } else if (elapsed < 5000) {
                status = 'Running BLAST';
            } else {
                status = 'COMPLETED';
            }

            console.log(`[Mock API] Checked status for ${job_id}: ${status}`);
            return { status };
        }

        const response = await fetch(`${BASE_URL}/status/${job_id}`);
        if (!response.ok) {
            throw new Error(`API Error: ${response.statusText}`);
        }
        return response.json();
    },

    async getResult(job_id: string): Promise<ProtocolResult> {
        if (USE_MOCK) {
            console.log(`[Mock API] Returning result for ${job_id}`);
            return MOCK_SUCCESS_RESULT;
        }

        const response = await fetch(`${BASE_URL}/result/${job_id}`);
        if (!response.ok) {
            throw new Error(`API Error: ${response.statusText}`);
        }
        return response.json();
    }
};
