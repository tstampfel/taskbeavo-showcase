# Research prompt template

Fill every `<...>`. Run the same text on Claude and Gemini at their highest research settings.

---

You are a research team: a Croatian porezni savjetnik, a Croatian and EU lawyer, and an analyst who studies how comparable companies operate. I need a decision, not general advice. Research the question below from primary sources, and tell me which option is most likely correct and why.

Today is <date>. Use the law in force now and from <decision date>. If you can't find or open a source, say "not found"; never invent a citation, an article number or a case.

## The question

<one sentence>

## The business (facts; do not assume anything beyond these)

<facts>

## Options

<O1 ... On, and "O_other: anything better you find">

## Opinions we already have (weigh them; don't repeat them)

<quoted opinions with dates, and whether they cite law>

## Evidence to look for, in this order

1. **Law in force.** Exact articles, with the Narodne novine issue (or EUR-Lex reference) and the wording quoted in the original language. Include implementing rules (pravilnici) and transitional provisions.
2. **Courts.** CJEU judgments and Croatian court decisions (Visoki upravni sud, upravni sudovi, Vrhovni sud) on the same or a close situation. Give the case number, date, link and the holding in one sentence.
3. **Tax-office and ministry practice.** Published Porezna uprava opinions (mišljenja) and Ministry of Finance or Labour statements, EU VAT Committee guidelines, explanatory notes to the law. Quote them.
4. **What comparable companies actually do.** This is important: companies with the same money flow have already paid for this advice, and their documents show the answer they got. Look at their terms of service, help pages, seller or host tax pages, DAC7 notices and press statements. Name the document and the date. Look at least at: <peer list>. For each, say what they do on this exact question and whether it is evidence for or against each option, and how close their situation is to ours (money flow, type of worker, country).
5. **Professional commentary** (Croatian tax journals such as RRiF, TEB, Porezni vjesnik; Big Four alerts). Lowest weight.

## Output (keep this structure; it is scored)

A. **Short answer:** the option you think is right, a probability band (e.g. 70–80%), and the one piece of evidence that decides it.

B. **Evidence table:** one row per claim: claim | supports option | source (exact reference and link) | tier (1 law/court/binding opinion, 2 official practice, 3 peer company, 4 cited professional, 5 uncited) | quote in the original language | your confidence the source says this (high/medium/low).

C. **Per option:** strongest evidence for, strongest against, probability band.

D. **Where the existing opinions are wrong or right**, point by point, with the source.

E. **Cost of being wrong** for each option (what TaskBeavo would owe or lose, penalties, how far back, whether it can be fixed), and what reduces the risk (binding opinion, contract wording, monitoring, a fallback switch).

F. **Open points:** what you could not find or verify, and the single question to put to the tax office in a binding-opinion request.

Mark every uncertain statement. Plain language, short sentences.
