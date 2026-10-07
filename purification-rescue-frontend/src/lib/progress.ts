import type { JobState, ProgressEntry } from './types/api';

// The pipeline's stages as the progress tracker shows them. The backend's
// progress messages are free text (backend.md, "Status values"), so a stage is
// recognized by the message's opening words rather than by exact match.

export interface Stage {
    label: string;
    description: string;
    /** Message prefixes that mean the pipeline has reached this stage. */
    prefixes: string[];
}

export const STAGES: Stage[] = [
    {
        label: 'Prepare',
        description: 'Preparing the environment and resolving the input...',
        prefixes: [
            'Initializing',
            'Retrieving internal data',
            'Fetching full sequence',
            'Analyzing user-provided'
        ]
    },
    {
        label: 'BLAST',
        description: 'Searching protein databases for homologs...',
        prefixes: ['Running BLAST', 'Strict search failed', 'Processing']
    },
    {
        label: 'Literature',
        description: 'Finding and reading purification protocols in the literature...',
        prefixes: ['Finding matching protocols', 'Analyzing Match', 'Listing papers']
    },
    {
        label: 'Synthesis',
        description: 'Synthesizing a protocol from the sources...',
        prefixes: ['Synthesizing', 'No articles found', 'Generating']
    }
];

/** The stage a progress message belongs to, or -1 if it isn't recognized. */
export function stageOfMessage(message: string): number {
    const text = message.trim().toLowerCase();
    return STAGES.findIndex(stage =>
        stage.prefixes.some(prefix => text.startsWith(prefix.toLowerCase()))
    );
}

/**
 * How far the job has got: the furthest stage any message in its history
 * reached, so an unrecognized message never moves the tracker backwards.
 * -1 before the first recognized message; STAGES.length once completed.
 */
export function currentStage(state: JobState, history: ProgressEntry[]): number {
    if (state === 'completed') return STAGES.length;
    return history.reduce((furthest, entry) => Math.max(furthest, stageOfMessage(entry.message)), -1);
}

/** The line under the tracker for a job in this state at this stage. */
export function describe(state: JobState, stage: number): string {
    switch (state) {
        case 'queued':
            return 'Waiting to start...';
        case 'completed':
            return 'Analysis complete. Redirecting...';
        case 'failed':
            return 'Analysis failed.';
        case 'interrupted':
            return 'The server restarted during this run, so the analysis stopped.';
        default:
            return STAGES[stage]?.description ?? 'Processing...';
    }
}

export const STATE_LABELS: Record<JobState, string> = {
    queued: 'Queued',
    running: 'Running',
    completed: 'Completed',
    failed: 'Failed',
    interrupted: 'Interrupted'
};
