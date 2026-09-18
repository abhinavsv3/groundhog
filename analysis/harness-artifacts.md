# Harness artifacts in published benchmark results

A coding-agent benchmark can fail in a way that produces a *plausible number*
rather than an error. A broken environment makes every task in one repository
fail; the submission still reports a total; nobody notices.

We hit four such bugs building Groundhog in two days:

| Bug | What it produced |
|---|---|
| Editable install shadowed the worktree | Every task passed — model edits were invisible |
| Premature completion accepted | Measured which model gave up politely |
| PEP 735 groups installed as extras | `pip` exited 0 having installed nothing; 0/12 tasks |
| Venv cache keyed only on dep files | Detection fixes silently had no effect |

None raised an error. All four produced believable results. So: is the same
thing visible in the public record?

## Method

For each submission *s* and repository *r*, model the success rate as

```
logit(p[s,r]) = ability[s] + easiness[r]
```

fitted by alternating maximum likelihood across all submissions and repos. A
submission that is *broken* on one repository — rather than merely weak there —
appears as a large negative Pearson residual on that cell while its other cells
fit. Residuals are standardised by binomial variance, so 0/44 is much stronger
evidence than 0/2.

**The test is tail asymmetry, not the raw count.** Binomial noise is symmetric,
so both tails should be equally populated. A broken environment can only make a
submission *fail*, never succeed. Comparing the two tails also controls for model
misspecification, since both come from the same fit.

## Result

122 submissions × 9 repositories = 1,098 cells, from the public
[SWE-bench experiments](https://github.com/SWE-bench/experiments) repository.

**Data integrity gate.** 13 submissions were excluded first. Their
`resolved_by_repo.json` carries full test-split denominators (2,294) rather than
the Verified subset (500), deflating their rates ~3.7x — independently reported
as [experiments#484](https://github.com/SWE-bench/experiments/issues/484).
Rather than hard-coding them, the loader takes the modal denominator per
repository as ground truth and drops disagreements; this recovers exactly those
13 and will catch whatever the next bookkeeping bug is. Fitting on them
manufactures outliers.

| z threshold | Below expectation | Above | Expected per tail | Enrichment |
|---|---|---|---|---|
| ≥ 2.0 | 17 | 14 | 25.0 | 0.7× |
| ≥ 2.5 | 10 | 2 | 6.8 | 1.5× |
| ≥ 3.0 | 5 | 1 | 1.5 | 3.4× |
| ≥ 3.5 | 3 | 1 | 0.3 | 11.7× |
| ≥ 4.0 | **2** | **0** | 0.03 | 57× |

At z ≥ 2 the tails are symmetric — that is noise. As the threshold rises the
negative tail pulls away monotonically while the positive tail stays at or below
chance. The bulk of the variation is ordinary; the extreme negative tail is not.

The two clearest cases:

| Observed | Expected | n | Repository | Submission |
|---|---|---|---|---|
| 3% | 65% | 34 | matplotlib | `20260901_mini-v2.4.2_gemini-3-5-flash` |
| 0% | 39% | 44 | sphinx | `20241125_enginelabs` |

A submission scoring **0 out of 44** on one repository while performing normally
on every other is not a capability profile. It is what a broken environment looks
like.

## What this does and does not show

**Does:** a statistical method for finding suspect cells in published results,
and evidence that the negative tail of SWE-bench submissions is enriched beyond
chance in the direction infrastructure failure predicts.

**Does not:** prove any specific submission had a harness bug. Only the
submitters' logs can do that. This says where to look.

**Alternative explanations worth ruling out first.** A flagged cell can also be a
bookkeeping error rather than a broken environment — see
[experiments#480](https://github.com/SWE-bench/experiments/issues/480), where a
submission's per-instance `resolved` labels were all false while the run itself
was real. For the two cells below, the submissions' own aggregates agree with
their per-repo sums, so the recorded numbers are at least internally consistent.

**Scale, honestly:** 2 of 1,098 cells at z ≥ 4, and 5 at z ≥ 3. Harness artifacts
in the public record are **detectable but rare** — not the widespread
contamination one might assume from how easily we produced four of them
ourselves. The contrast is itself interesting: careful submitters mostly get this
right, and the failures that survive are concentrated and severe rather than
diffuse.

## Reproducing

```bash
git clone --filter=blob:none --sparse https://github.com/SWE-bench/experiments
python analysis/artifacts.py experiments
```
