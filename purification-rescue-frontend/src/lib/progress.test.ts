import { describe, expect, it } from 'vitest';
import { currentStage, describe as describeStage, STAGES, stageOfMessage } from './progress';

const at = '2026-09-23T10:00:00Z';

describe('stageOfMessage', () => {
    it.each([
        ['Initializing Agent...', 0],
        ['Running BLAST (Strict: 40.0% Cov)...', 1],
        ['Strict search failed, relaxing thresholds...', 1],
        ['Finding matching protocols in PMC...', 2],
        ['Synthesizing final protocol with LLM...', 3],
        ['Something new the backend says', -1]
    ])('%s -> stage %i', (message, stage) => {
        expect(stageOfMessage(message)).toBe(stage);
    });
});

describe('currentStage', () => {
    it('is -1 before any recognized message', () => {
        expect(currentStage('queued', [])).toBe(-1);
    });

    it('never moves backwards on a later unrecognized or earlier message', () => {
        const history = [
            { at, message: 'Running BLAST...' },
            { at, message: 'Finding matching protocols in PMC...' },
            { at, message: 'Something new' },
            { at, message: 'Initializing Agent...' }
        ];
        expect(currentStage('running', history)).toBe(2);
    });

    it('is past the last stage once the job completes', () => {
        expect(currentStage('completed', [])).toBe(STAGES.length);
    });
});

describe('describe', () => {
    it('uses the stage description while running', () => {
        expect(describeStage('running', 1)).toBe(STAGES[1].description);
    });

    it('falls back to a generic line for an unknown stage', () => {
        expect(describeStage('running', -1)).toBe('Processing...');
    });

    it('describes stopped jobs by state', () => {
        expect(describeStage('interrupted', 1)).toMatch(/restarted/);
        expect(describeStage('failed', 1)).toBe('Analysis failed.');
    });
});
