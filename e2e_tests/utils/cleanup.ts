/**
 * Teardown registry for data a spec creates on a shared instance. Register a
 * step right after each create, then call `run()` from `afterAll`/`afterEach`.
 */
export class Cleanup {
  private readonly steps: Array<{
    label: string;
    fn: () => Promise<unknown>;
  }> = [];

  defer(label: string, fn: () => Promise<unknown>): void {
    this.steps.push({ label, fn });
  }

  /** Runs steps newest-first. A failing step is logged, never thrown, so it can't mask the test result. */
  async run(): Promise<string[]> {
    const failures: string[] = [];
    for (let step = this.steps.pop(); step; step = this.steps.pop()) {
      try {
        await step.fn();
      } catch (err) {
        const message = `Cleanup: ${step.label} failed: ${err}`;
        console.warn(message);
        failures.push(message);
      }
    }
    return failures;
  }
}
