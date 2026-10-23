# ADR 0004: Artifact QA is heuristic, previewed and human-gated

Status: accepted, 2026-10-23

- **No artifact-free promise.** Analysis flags colour cast, noise and (advisory only) waxy skin, then offers masked, bounded repairs. Every analysis report states that automated scores are fallible.
- **Cast detection** looks for neutral surfaces. If truly neutral surfaces exist there is no global cast. Several distinct neutral clusters shifted the same way is a confident cast (warning or critical). A single warm cluster is a *review* hint only: a beige wall and a yellow cast cannot be told apart from one colour. Found by testing a plain beige proxy, which the first version wrongly flagged.
- **Protection:** saturated pixels (lamps, neon, signs) are excluded from correction by a smooth chroma weight, and untouched pixels stay byte-identical (white stays 255).
- **Noise** is measured only inside flat blocks, so rust, corrugation and fabric texture are not noise. Reduction is feathered to those blocks.
- **Skin** is never auto-fixed. The repair endpoint accepts only `cast` and `noise`.
- **Repairs** are previewed (before, after, diff heatmap, mask), applied only on accept, logged with parameters and the user's note, and the original is kept so it can be restored. The view returns to NEEDS_REVIEW.
- **Correction bound:** at most 14 Lab units, so a severe cast stays flagged and needs a re-render, which is the preferred fix anyway.
- **Export gate:** unresolved critical findings on the current files make the asset incomplete unless the user gives a written override, which is stored in the report.
- **Not done:** OCR text checks, shape/part-count comparison against the source, halo and jaggies detection.
