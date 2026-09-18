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

131 submissions × 11 repositories = 1,183 cells, from the public
[SWE-bench experiments](https://github.com/SWE-bench/experiments) repository.

| z threshold | Below expectation | Above | Expected per tail | Enrichment |
|---|---|---|---|---|
| ≥ 2.0 | 23 | 21 | 26.9 | 0.9× |
| ≥ 2.5 | 14 | 7 | 7.3 | 1.9× |
| ≥ 3.0 | 7 | 2 | 1.6 | 4.4× |
| ≥ 3.5 | 4 | 1 | 0.3 | 14.5× |
| ≥ 4.0 | **2** | **0** | 0.04 | 53× |

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

**Scale, honestly:** 2 of 1,183 cells at z ≥ 4, and 7 at z ≥ 3. Harness artifacts
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
