# Writing card text: explanations and examples

How to write a card's English explanation and example sentences when they are written for the deck rather than looked up (#621). The rules come from two places: what the games need from the text, and the shape of the curated B2–C1 deck, measured in October 2026, so that new cards read like the ones learners already know.

## Inputs

For each card, write from:

1. the **word** and its **part of speech**;
2. the card's **Ukrainian and Russian translations**, which say which sense the card is about;
3. the word's **Wiktionary entry** (`parsers._wiktionary_entry()`), which is CC BY-SA 4.0;
4. general knowledge of English.

If a card cannot be done from these, mark it `skip` with a reason.

## The staging format

One JSON object per line (JSONL), keyed by word and part of speech rather than by card id, because ids differ between a local database and PythonAnywhere:

```json
{"word": "remedy", "pos": "noun", "sense": "засіб, ліки, спосіб",
 "explanation_en": "...", "explanation_source": "wiktionary",
 "examples_en": ["...", "...", "..."], "examples_source": "ai",
 "status": "done", "notes": ""}
```

- `sense`: the Ukrainian translation used to choose the sense, copied from the card.
- `explanation_source` / `examples_source`: `wiktionary` (Wiktionary's text, verbatim), `wiktionary-adapted` (written from Wiktionary's sense; credited as adapted, #618) or `ai` (written without a Wiktionary sense to follow).
- `status`: `done`, `check` (unsure, for a person to look at) or `skip` (with `notes` saying why).

## The explanation

**Prefer Wiktionary verbatim.** If one of its definitions for this part of speech matches the card's sense and suits a B2 learner, copy it exactly: up to three senses joined with "; ", as `lookup_word()` does. Change nothing, not even punctuation; the card credits it (#390).

**Check the sense against the topic first.** The card's translation and its topic say which sense learners need. If Wiktionary does not have that sense, write it yourself (`ai`) instead of taking the sense Wiktionary does have: a Work and careers card for *appraisal* needs the review-of-your-work sense, not only the general "judgement of value".

**Otherwise write one** (`wiktionary-adapted` or `ai`):

- one sense, the one the translation points to; **two when both are common** and a learner will meet both (a word whose second sense is everyday in the card's topic), joined with "; ". Both are senses of the card's own part of speech: another part of speech is always another card;
- **7–14 words**, never over 20;
- short, everyday words; a long word only when there is no plain one;
- **does not contain the word or any form of it**, which makes a better clue in *Spell it*;
- begins the usual way for its part of speech:
  - verb: "to …", often "to make somebody or something …" for a causative verb;
  - noun: "a / an / the …", with the stock frames "the act of …ing", "the process of …", "a person who …", "a feeling of …";
  - adjective: "having …", "not …", "related to …", "connected with …", "that …";
- "somebody or something" written out, not with a slash; no brackets, no "=" notes, no "etc.";
- British spelling (*colour, organise, judgement*).

## The examples

Always newly written, `examples_source` = `ai`. Three per card; two only for a fixed phrase where a third would be forced.

Each example:

- contains the word, or an inflected form of it, **exactly once**: *Fill the gap* cuts it out, so it has to be there;
- is a complete sentence of **6–12 words** (up to 14 when the word needs context), with a capital letter and a full stop: *Rebuild the sentence* needs 5–15 words, and a phrase cannot be rebuilt;
- is one clause, with no commas by default;
- uses **the same sense as the explanation** above it, not another sense of the same word; check all three against the explanation before moving on;
- across the three, **one example carries the word's best-known fixed phrase or pattern** when it has one (*on prescription*, *an outbreak of …*, *the dosage of …*), in a sentence of your own;
- is a plain statement; across a card, mix past and present, and use a question or a negative only now and then (about one card in five);
- varies its subject across the three: "The …", "He", "She", "We", "They";
- shows the word with its usual partner words: a noun with its usual preposition (*of, for, to, in, on*), a verb with a direct object or its usual preposition; and does not always put the word last;
- has no brackets, slashes, quotation marks or semicolons (#614), and no real people, companies or brands;
- is B2–C1 level, about everyday life, work, study, news or relationships;
- is British English.

## The deck's shape, for reference

Measured in October 2026 on the curated deck (local, 555 cards; live, 462 cards):

| | Measured | Written to |
|---|---|---|
| senses per card | mostly 1–3 | 1, sometimes 2–3 |
| words per sense | median 9 (local) to 12 (live); 90% under 19 | 7–14 |
| words of 10+ letters in a definition | 6% | as few as possible |
| examples per card | usually 3 | 3 |
| words per example | median 8; 90% under 12 | 6–12 |
| examples that are full sentences | about 68% | 100% |
| statements among full sentences | about 92% (questions 3%, negatives 5%) | mostly statements |
| examples with a comma | 5% | none by default |
| past tense | about half | about half |
| first word of an example | "The" 25%, "He" 10%, "She" 8%, "I" 5%, "We" 4% | varied |
| where the word sits | first third 30%, middle 36%, last third 34% | spread |

## Working method

1. Batches of 20–30 cards, one topic at a time; append to the staging file and never rewrite earlier lines.
2. For each card: read the word, part of speech and translation; fetch Wiktionary; take the explanation verbatim or write one; write three examples; check them against the lists above.
3. Run the checker after each batch (#621): the word present exactly once per example, 6–14 words, full sentence, no forbidden characters, at most three senses and 20 words per sense, valid source values, no duplicate word + part of speech.
4. Mark anything uncertain `check` rather than guessing; idioms and words with several close senses are the usual cases.
5. A person reads a sample of every batch before it is applied. When a review changes a rule, change it here in the same step.

## Worked example

Card: `thrive`, verb, translated *процвітати, розквітати, гарно розвиватися*.

- **Explanation:** Wiktionary has two senses. The first, about growing vigorously, is long and uses rare words; the second, about increasing in wealth or success, matches *процвітати* and reads easily. So the second is copied verbatim, alone, with `explanation_source: "wiktionary"`. Choosing the sense is part of the job: the translation decides it.
- **Examples** (`ai`):
  - "Small cafés usually thrive in busy university towns."
  - "Her team thrived after the new manager arrived."
  - "They believe children thrive on a clear daily routine."
