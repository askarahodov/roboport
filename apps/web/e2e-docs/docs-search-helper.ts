import { expect, type Page } from '@playwright/test';

/**
 * Docsify 5 indexes the sidebar's markdown asynchronously, especially slowly
 * on Windows. An early input event may produce an empty result panel without
 * being replayed when indexing finishes. Poll a REAL search, requiring a
 * visible result link; never mock the index or skip the assertion.
 */
export async function expectIndexedDocsSearch(page: Page, query: string): Promise<void> {
  const field=page.locator('.sidebar .search input');
  const matches=page.locator('.sidebar .results-panel a');
  await expect(field).toBeVisible();
  await expect.poll(async()=>{
    // Explicitly retrigger input after Docsify may have finished indexing.
    await field.fill('');
    await field.fill(query);
    return await matches.count();
  },{timeout:30_000,intervals:[1000,1500,2000,3000]}).toBeGreaterThan(0);
  await expect(matches.first()).toBeVisible();
}
