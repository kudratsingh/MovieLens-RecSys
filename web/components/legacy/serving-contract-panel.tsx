import type { ServingPolicy } from "@/lib/api";

/**
 * The legacy dashboard's serving-contract panel.
 *
 * It used to be three hard-coded rows, and one of them — `Candidate policy:
 * Popularity baseline` — was a claim the deployed router contradicted in the
 * same session that rendered it. A model claim that exceeds observed backend
 * behaviour is a forbidden default in the design contracts, and being behind a
 * legacy route does not make it less false, so the panel now reports the
 * `serving_policy` the response carried and says nothing when it has none.
 *
 * The two remaining rows are deliberately not model claims. Isolation is a
 * deployment fact the tenant-isolation suite proves, and the latency row is
 * labelled as the target it is rather than as a measurement.
 */
export function ServingContractPanel({
  policy,
  modelVersion,
}: {
  /** `null` until the first response lands, or if the read failed. */
  policy: ServingPolicy | null;
  modelVersion: string | null;
}) {
  return (
    <aside
      className="rounded-2xl border border-white/10 bg-white/[0.035] p-5"
      data-testid="serving-contract"
    >
      <p className="text-xs uppercase tracking-[0.2em] text-zinc-400">Serving contract</p>
      <dl className="mt-5 space-y-4 text-sm">
        <div className="flex justify-between gap-6">
          <dt className="text-zinc-400">Serving policy</dt>
          <dd data-testid="serving-contract-policy">
            {policy ? policy.name : "Not read yet"}
          </dd>
        </div>
        <div className="flex justify-between gap-6">
          <dt className="text-zinc-400">Learned ranking</dt>
          <dd>
            {policy
              ? policy.learned
                ? "Yes"
                : `No — ${policy.positive_signal_count} of ${policy.threshold} positive signals`
              : "Not read yet"}
          </dd>
        </div>
        <div className="flex justify-between gap-6">
          <dt className="text-zinc-400">Model version</dt>
          {/* A model version is one long unspaced token; without this it sets
              the panel's minimum width and takes the phone viewport with it. */}
          <dd className="min-w-0 break-words text-right font-mono text-xs">
            {modelVersion ?? "Not read yet"}
          </dd>
        </div>
        <div className="flex justify-between gap-6">
          <dt className="text-zinc-400">Isolation</dt>
          <dd>Postgres RLS</dd>
        </div>
        <div className="flex justify-between gap-6">
          <dt className="text-zinc-400">Latency target</dt>
          <dd className="font-mono text-amber-300">p99 &lt; 100 ms</dd>
        </div>
      </dl>
      {policy ? (
        <p className="mt-5 text-xs leading-5 text-zinc-400">{policy.reason}</p>
      ) : null}
    </aside>
  );
}
