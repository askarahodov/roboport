/** Minimal Node type surface for vite.config.ts.
 * The project deliberately has no @types/node dependency in its frozen lockfile.
 * These are type declarations only; Node's real fs/process APIs run at build time.
 */
declare module 'node:fs' {
  export function readFileSync(path: string, encoding: 'utf8'): string;
}
declare const process: {
  readonly env: Readonly<Record<string, string | undefined>>;
};
