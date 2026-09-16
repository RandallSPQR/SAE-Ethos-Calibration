# Arm B — disguised-harm compliance (Dictatorship Eval Level-6 extension)

**Not a reproduction of the Mythos card.** This arm asks a question the card doesn't: when a model
performs a mundane edit inside an explicitly harmful codebase (Dict Eval Level 6), does any internal
awareness feature — policy-violation, ethical-concern, concealment, evaluation-awareness — fire at
all, or is compliance internally *silent*? A silent result would say the obfuscation works at the
representational level, not just behaviorally. That's the novel contribution, and it's Randall + Hall,
not a card replication.

## Sourcing — items are never authored here

The harmful codebases come from the published Dictatorship Eval (dictatoreval.org), which posts its
Level-6 scenarios for community extension. This repo contains **no** authoritarian codebase content.
`items.yaml` references items by the eval's own IDs; a loader the experimenter runs (`load_items.py`
in the pipeline repo, pointed at a local checkout of the eval's released scenarios) resolves them.
If an ID can't be resolved, the item is skipped, not reconstructed.

Rationale: the point of the arm is measurement, and the neutralization machinery (`twin_rules.yaml`)
plus the gate (`gate_b0.md`) are the parts with research value. Reproducing the harmful text here adds
nothing and is avoided.

## Twin design

Every Level-6 item is run as a matched pair, judged blind:

- `bad/` — the original item: benign-looking edit request over a codebase whose names/comments make
  its authoritarian purpose plain (`dissident_targeting`, `social_credit_score`, etc.).
- `twin/` — the *identical edit* over a codebase where `twin_rules.yaml` has neutralized the purpose
  to something mundane (`delivery_routing`, `loyalty_points`), leaving structure, symbol arity, and
  the requested change unchanged.

The contrast bad − twin isolates the internal effect of the *harmful framing* while holding the code
task constant. Behaviorally the card's finding predicts both comply; the interesting signal is
whether the awareness features differ between bad/ and twin/.

## What Arm B measures

- **Behavioral:** compliance score (Dict Eval 5-point, `common/judge_rubric_B.md`) + verbalized
  awareness. Expected: near-total compliance in both variants (replicating the eval).
- **Internal:** mean activation of each `features_of_interest.yaml: arm_b` feature on the edit turn,
  bad/ vs twin/, plus the evaluation-awareness probe from §4.5.5.
- **Headline test (B0):** does *anything* — behavior or feature — distinguish bad/ from twin/, or from
  an Arm-A benign baseline? If not, the model is complying to authoritarian code with no internal
  trace, which is the result worth reporting.

## Isolation

Arm B has its own judge, feature list, output directory, and hand-label set. It is never pooled with
Arm A. Feature *discovery* uses synthetic contrastive sets (per §4.5.2), never Arm-A transcripts.
