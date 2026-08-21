import { resetReport } from "./measure";

/**
 * Start every run from an empty report.
 *
 * `writeReport` merges on write so a retired worker cannot lose the routes it
 * already measured. That merge would otherwise also inherit routes from the
 * previous run, which is how a report starts describing a stack that is no
 * longer there.
 */
export default function globalSetup(): void {
  resetReport();
}
