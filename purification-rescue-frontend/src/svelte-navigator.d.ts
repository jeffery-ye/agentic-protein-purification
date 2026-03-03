declare module 'svelte-navigator' {
    import { SvelteComponentTyped } from 'svelte';

    export class Router extends SvelteComponentTyped<any, any, any> { }
    export class Route extends SvelteComponentTyped<any, any, any> { }
    export class Link extends SvelteComponentTyped<any, any, any> { }

    export function useNavigate(): (to: string, options?: any) => void;
    export function useParams(): { subscribe: (run: (value: any) => void) => () => void };
}
