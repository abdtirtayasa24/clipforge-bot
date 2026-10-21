import type { CostOut } from "../api/types";
import { formatCost } from "../format";

/** One line per currency; usage in several currencies is never summed together. */
export function CostList({ costs }: { costs: CostOut[] }) {
  if (costs.length === 0) {
    return <span className="hint">No AI usage recorded.</span>;
  }
  return (
    <>
      {costs.map((cost) => (
        <div key={cost.currency}>{formatCost(cost)}</div>
      ))}
    </>
  );
}
