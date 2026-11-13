---
name: legal-decision
description: Break a deadlock on a TaskBeavo legal, tax or hard-rule question when opinions conflict (accountant vs AI, lawyer vs advisor, Claude vs Gemini). Runs one evidence-ranked research round on Claude and Gemini with the same prompt, covering law in force, court and tax-office practice, and what comparable companies actually do; verifies every citation; scores each option by evidence tier and probability, decides by expected cost of being wrong, and writes a decision record in docs/decisions/ that says what would reopen it. Use this whenever two or more opinions disagree, someone suggests "get another opinion", a question has gone round more than once, or the user says "decide", "break the deadlock", "rank the opinions", "what do other companies do", even if the skill isn't named.
---

# Legal decision

Opinions that disagree are not solved by collecting more opinions. They are solved by weighing the ones we have against the law, against practice, and against what similar businesses already do, and then deciding. This skill is that method. It produces a decision, not advice; the decision names its residual risk and how to reduce it.

This is not legal advice. It records how TaskBeavo decided under uncertainty, so the reasoning can be checked later by a lawyer, an advisor or the tax office.

## When it applies

- Two or more opinions conflict on a legal, tax, consumer, privacy or hard-rule (CLAUDE.md a–k) question.
- Someone proposes a further opinion on a question that already has two.
- A question has been asked and answered more than once without a decision.

Rule: **one research round per question.** After it, decide. A new round is allowed only when the decision record names the trigger that has happened (a ruling, a law change, a binding opinion, a new material fact).

## Steps

### 1. Frame the question

Write `docs/decisions/<id>-<slug>/question.md`:
- the question in one sentence, and why it matters (money, product shape, launch);
- the facts of the business that the answer depends on (copy them from CLAUDE.md and PLAN.md; never let a model guess them);
- the options, numbered O1, O2, …, including "something else" so the research can add one;
- every opinion so far, quoted, with its date and whether it cites law;
- the cost of being wrong for each option, in plain words (what we would owe or lose, and whether it can be fixed later).

### 2. Build the prompt

Fill `research-prompt-template.md` from this skill into `docs/decisions/<id>-<slug>/prompt.md`. Don't shorten its evidence and output sections; the scoring depends on them. The user runs the same prompt on Claude and on Gemini at their highest research settings (Claude: Research / extended thinking; Gemini: Deep Research), and the agent may run it too with web search. Save each answer as `answer-claude.md`, `answer-gemini.md` (PDF text is fine).

### 3. Verify the citations

Before scoring, check every claim an answer rests on against the source itself:
- law: the official text in force for the decision date (Narodne novine, EUR-Lex, consolidated text on zakon.hr only as a pointer, the NN issue is the source);
- tax-office practice: the published opinion on porezna-uprava.gov.hr;
- court: the decision (CJEU on curia.europa.eu, Croatian courts via sudskapraksa.csp.vsrh.hr or odluke.sudovi.hr);
- peer practice: the company's own terms, help pages or filings, with the date read.

Mark each claim **verified** (the source says it), **partial** (the source exists but says less or something different), **failed** (no such source, or it says the opposite), or **unchecked**. Write the result into `verification.md` as a table: claim, option it supports, source, tier, status, quote in the original language.

### 4. Score

Evidence tiers and weights:

| Tier | Source | Weight |
|---|---|---|
| 1 | Law text in force; CJEU and Croatian court decisions; a binding opinion (obvezujuće mišljenje) | 1.0 |
| 2 | Published Porezna uprava or ministry opinions; EU VAT Committee guidelines; official explanatory notes | 0.8 |
| 3 | What comparable companies actually do, shown by their own documents (platforms with the same money flow or the same kind of worker, in Croatia first, then the EU) | 0.6 |
| 4 | A named licensed professional (porezni savjetnik, odvjetnik, ovlašteni računovođa) whose opinion cites its sources | 0.5 |
| 5 | Any opinion without citations, AI or human; commentary and blogs | 0.1 |

Verification multiplier: verified 1.0, partial 0.5, unchecked 0.3, failed 0. The same source cited by several answers counts once. A tier-5 opinion never outweighs a verified tier 1–3 claim, however confident it sounds.

For each option, add the weighted support and the weighted contrary evidence, and turn the balance into a probability band (for example 70–80%), not a falsely exact number. Say which single piece of evidence moves it most.

### 5. Decide by expected cost

The most probable option does not win on its own when the costs of being wrong differ a lot. For each option: P(wrong) × cost if wrong, and whether the cost can be capped (reversible, mitigated, insured, time-limited). Choose the lowest expected cost. On a tie, choose the option that is easier to reverse.

Add the cheap risk reducers that don't block the decision: a binding opinion filed in parallel, a monitor or threshold alert, wording in the Terms, a feature flag that switches to the fallback option.

The user makes the final call on hard-rule, money, legal and privacy decisions; the skill brings them the scored table and a recommendation.

### 6. Record

Write `docs/decisions/<id>-<slug>/decision.md` from `decision-record-template.md`: the question, the options, the verified evidence table, the probabilities, the expected-cost table, the decision and who made it, the risk reducers, and **reopen triggers** (only these reopen it). Link it from `docs/decisions/README.md`, from the Jira ticket and from the legal list (BEAV-39). Code and PLAN.md follow the decision; CLAUDE.md changes only when the user says so.

## Notes

- Peer practice is evidence, not proof. A large company may simply carry the risk, or have a ruling we can't see. Prefer peers with the same money flow (a platform collecting payment for private individuals) and the same country.
- If the research finds a fact about TaskBeavo that is wrong in the prompt, fix the facts and rerun only the affected part.
- Keep quotes in the original language; translate beside them.
