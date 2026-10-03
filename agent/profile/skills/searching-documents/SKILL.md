---
name: searching-documents
description: Finds cited passages in the selected document sources
license: Apache-2.0
compatibility: Requires the retrieval MCP server (retrieve_evidence tool)
metadata:
  author: NVIDIA
  version: "2.0"
  hermes:
    tags:
      - retrieval
      - documents
      - citations
      - nemotron
    related_skills:
      - querying-tables
      - predicting-with-kumo
---

# Searching documents

`retrieve_evidence` searches every document source selected for this turn:
policies, procedures, contracts, reports, manuals, slide decks and scanned
pages, parsed (with NVIDIA Nemotron Parse where a page needs it) and split into
passages. It ranks passages with NVIDIA Nemotron retrieval models and returns
up to 8, each with its document title, citation and metadata.

## When to Use

- The selected sources include the `unstructured_retrieval` capability, and
- the answer depends on what a document says: a rule, a requirement, a
  procedure step, a term of a contract, a figure stated in a report, or a
  quotation.

Figures computed from table rows (counts, totals, rankings) belong to
`querying-tables`. Forecasts belong to `predicting-with-kumo`.

## Tool

`retrieve_evidence` (Hermes tool ID `mcp__retrieval__retrieve_evidence`)

| Argument | Value |
| --- | --- |
| `query` | A focused description of the passage you need |
| `top_k` | Leave unset: it returns 8 passages, the most one call returns |

The application limits the search to the selected sources. Pass only `query`.

Each hit has a `rank`, a `score`, the `source_id`, the document `title`, a
`snippet` (the passage text) and `metadata`: `citation` (file and page or
section), `file_name`, `page_start` and `page_end` when the document has pages,
`headings` (where the passage sits in the document) and `doc_items` (`table`
when the passage holds a table). `models.rerank` is null when the search ran
without a reranker; the hits are then in vector-similarity order.

## Procedure

1. Write one focused query per distinct topic, in the words the document
   itself would use: "restocking fee for opened electronics", not "how much
   does it cost to return a laptop". Name the concept you need, not the answer
   you expect. To find one document, put its subject or title in the query.
2. For an event, a condition or a requirement, write the query as the sentence
   the document would use to state it, such as "a technician must lock out the
   machine before clearing a jam". A bare keyword list matches tables of
   contents, glossaries and lists of headings instead of the passage that says
   it.
3. Make one call even when several document sources are selected. Their
   passages are ranked together.
4. Keep only passages that directly support a claim, and note each passage's
   title, citation and section heading. A passage cut off mid-sentence ends in
   an ellipsis: claim only what the text shown says.
5. A passage can hold a table as Markdown rows (its `doc_items` include
   `table`). Read the header row and the row label before you cite a value from
   it, and keep the table's units.
6. If nothing relevant comes back, rephrase once with different key terms,
   such as a synonym the document might use. Then say the passages found do
   not cover the question. A call returns only its best passages, so never
   claim that a source contains no such document.
7. That makes at most two searches per topic. A question about a policy and
   the procedure that applies it has two topics: search the policy, then the
   procedure. When the passages cover only part of a rule, cite what they show
   and name what is missing rather than searching again.

## Pitfalls

- Keep what a document's author claims separate from what a rule or policy
  requires, and a draft or proposal separate from a policy in force.
- When two documents disagree (an old and a new version, a policy and a
  procedure), report both with their citations and dates.
- Quote exact wording only when the wording matters. Otherwise paraphrase and
  cite.
- Never answer a document question from general knowledge or web search.
- Link a document to rows in a table only through an identifier both share,
  such as a product code or account number. A matching name alone does not
  prove it is the same entity.

## Example

Question: "What restocking fee applies to opened electronics, and do gold
members pay it?"

```
retrieve_evidence(query="restocking fee for opened electronics and loyalty tier exceptions")
```

Question: "What must a technician do before clearing a jammed conveyor?"

```
retrieve_evidence(query="the technician must lock out and tag out the conveyor before clearing a jam")
```

The second query is the sentence a safety procedure would contain. A keyword
list such as "conveyor jam safety" tends to return the manual's contents page
and section titles instead.

Answer from the returned passages and cite the result's `evidence_id` after each
claim as `[evidence:<evidence_id>]`. One token covers every passage of the
result: do not add a passage's rank, title or page inside the brackets.
