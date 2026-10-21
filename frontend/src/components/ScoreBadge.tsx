/** A clip's total score out of 100, coloured green / amber / red by strength. */
export function ScoreBadge({ score }: { score: number }) {
  const level = score >= 75 ? "" : score >= 50 ? " mid" : " low";
  return (
    <span className={`score${level}`}>
      <strong>{score}/100</strong>
      <span>Score</span>
    </span>
  );
}
