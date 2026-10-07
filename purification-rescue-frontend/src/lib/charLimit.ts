/**
 * Characters left before a limit, negative when over it. Counts code points,
 * as the backend's max_length does, rather than JavaScript's UTF-16 units.
 */
export function charactersLeft(text: string | null | undefined, max: number): number {
    return max - Array.from(text ?? '').length;
}
