import { describe, expect, it } from 'vitest';
import { charactersLeft } from './charLimit';

describe('charactersLeft', () => {
    it('counts down from the limit', () => {
        expect(charactersLeft('', 10)).toBe(10);
        expect(charactersLeft('abc', 10)).toBe(7);
    });

    it('treats a missing value as empty', () => {
        expect(charactersLeft(null, 10)).toBe(10);
        expect(charactersLeft(undefined, 10)).toBe(10);
    });

    it('goes negative over the limit', () => {
        expect(charactersLeft('abcdef', 4)).toBe(-2);
    });

    it('counts a character outside the BMP once, as the backend does', () => {
        expect(charactersLeft('🧪', 10)).toBe(9);
    });
});
