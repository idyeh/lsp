# AGENTS.md

This file defines the working rules for coding agents in this repository.

The purpose is simple: make correct, reviewable changes without damaging existing behaviour.

## 1. Understand Before Editing

Before changing code:

1. Read the relevant implementation, tests and configuration.
2. Identify the existing conventions before introducing new ones.
3. Trace the code path far enough to understand its callers, outputs and side effects.
4. Check whether the behaviour is part of a public API, data contract or user workflow.
5. Look for existing utilities before creating another abstraction.

Do not modify code merely because it looks unusual. Existing behaviour may encode requirements that are not obvious from one file.

When uncertain, prefer investigation over assumption.

---

## 2. Keep the Change as Small as Possible

Make the smallest coherent change that solves the stated problem.

Do not:

- refactor unrelated code;
- rename unrelated symbols;
- reformat entire files unnecessarily;
- replace established libraries without a clear need;
- introduce new architecture for a local problem;
- implement speculative future requirements.

A bug fix should normally look like a bug fix. A small feature should not turn into a framework migration.

Small diffs are easier to review, test and reverse.

---

## 3. Preserve Contracts and Invariants

Treat existing interfaces as contracts unless the task explicitly requires changing them.

This includes:

- API routes and request/response formats;
- function signatures;
- database constraints;
- configuration keys;
- file formats;
- command-line behaviour;
- error semantics;
- externally visible identifiers.

Before changing a contract, inspect its consumers.

Maintain important invariants in the layer that owns them. Do not rely solely on the user interface to enforce business rules.

Validate assumptions at system boundaries.

---

## 4. Prefer Simple, Explicit Code

Optimise first for correctness and readability.

Prefer:

- straightforward control flow;
- descriptive names;
- small functions with clear responsibilities;
- explicit data transformations;
- established project patterns.

Avoid:

- unnecessary metaprogramming;
- premature generalisation;
- clever one-liners that obscure intent;
- deeply nested logic;
- hidden mutable state;
- abstractions with only one hypothetical future use.

Duplication is sometimes cheaper than the wrong abstraction.

Create an abstraction when the common behaviour is already understood, not merely anticipated.

---

## 5. Handle Data Carefully

Persistent data requires greater caution than application code.

When changing a schema:

- use a migration where the project supports migrations;
- preserve existing data unless deletion is explicitly required;
- consider nullability, defaults and uniqueness;
- consider both upgrade and rollback behaviour;
- avoid silently changing the meaning of existing fields.

Never assume that a development database represents all valid production data.

Do not rewrite historical data simply to make new code easier.

For destructive operations, require explicit intent and make the scope clear.

---

## 6. Security Is a Requirement

Never commit:

- passwords;
- API keys;
- tokens;
- private certificates;
- connection credentials;
- real user secrets.

Use configuration or environment variables for secrets.

Treat all external input as untrusted.

Apply appropriate:

- authentication;
- authorisation;
- input validation;
- output escaping;
- query parameterisation;
- path validation.

Do not weaken security controls to make development easier.

Do not expose internal exceptions, stack traces, credentials or sensitive records through user-facing responses.

Use established cryptographic and password-handling libraries. Do not invent security mechanisms.

---

## 7. Dependencies Must Earn Their Place

Before adding a dependency, ask whether the existing standard library or project dependencies already solve the problem adequately.

When adding one:

- use a maintained and reputable package;
- keep the version constraint consistent with project practice;
- avoid pulling in a large framework for a small feature;
- update dependency metadata and lock files as required;
- verify licences where relevant.

Do not upgrade unrelated dependencies as part of another change unless required for compatibility or security.

---

## 8. Errors Must Be Deliberate

Expected failure conditions should be handled explicitly.

Use errors that help callers understand what failed without exposing unnecessary internal details.

Do not:

- swallow exceptions silently;
- catch broad exceptions without a clear reason;
- convert every error into a successful response;
- use logging as a substitute for error handling.

Fail early when continuing would produce invalid state.

Preserve the original cause when wrapping exceptions where practical.

---

## 9. Logging Should Explain Events

Log information that is useful for diagnosis.

Good logs usually answer:

- what operation was attempted;
- what resource was involved;
- what significant state transition occurred;
- why an operation failed.

Avoid logging:

- passwords;
- tokens;
- private personal data;
- complete request bodies without a clear reason;
- noisy messages inside normal tight loops.

Use appropriate log levels. Routine successful operations should not produce error-level logs.

---

## 10. Test Behaviour, Not Implementation Trivia

Changes should be accompanied by tests when they alter behaviour or fix a defect.

At minimum:

- run existing relevant tests;
- add a regression test for a confirmed bug where practical;
- test important success and failure paths;
- test boundary conditions where they matter.

Prefer tests that describe observable behaviour.

Avoid tests that depend unnecessarily on private implementation details.

A failing test should provide useful evidence. Do not weaken or delete a legitimate test merely to make the suite pass.

If a test expectation is genuinely wrong, explain why it must change.

---

## 11. Consider State and Concurrency

Code that reads, checks and then writes shared state may be unsafe under concurrent access.

Where applicable, consider:

- database transactions;
- uniqueness constraints;
- idempotency;
- race conditions;
- duplicate requests;
- retries;
- partial failure.

Prefer enforcing critical uniqueness and integrity rules in the database as well as in application logic.

Do not assume that an operation will only be executed once.

---

## 12. Respect Existing Architecture

Follow the repository's existing separation of concerns.

Do not move business logic into presentation code simply because it is convenient.

Typical responsibilities should remain clear:

- presentation handles interaction;
- application or service code handles business rules;
- persistence code handles storage;
- infrastructure code handles external systems.

Do not introduce a new architectural layer unless it solves a concrete problem.

---

## 13. Keep Version-Control Changes Reviewable

Each change should have a clear purpose.

Before finishing:

- inspect the complete diff;
- remove debugging code;
- remove accidental generated files;
- check that unrelated files were not modified;
- make sure comments and names still match the implementation.

Do not include broad formatting changes with functional changes unless formatting is the task.

Never rewrite repository history unless explicitly requested.

Do not discard another contributor's uncommitted work.

---

## 14. Documentation Must Match Reality

Update documentation when a change affects:

- installation;
- configuration;
- public APIs;
- data formats;
- operator procedures;
- user-visible behaviour.

Document decisions that are difficult to infer from the code.

Do not write comments that merely restate the syntax.

Prefer explaining:

- constraints;
- assumptions;
- non-obvious trade-offs;
- reasons for apparently unusual decisions.

Outdated documentation is worse than concise documentation.

---

## 15. Do Not Pretend Work Has Been Verified

Never claim that code:

- compiles;
- runs;
- passes tests;
- fixes a bug;
- preserves compatibility

unless the relevant verification has actually been performed.

If verification cannot be completed, state exactly what remains unverified.

Do not fabricate test output, execution results, files, API responses or database state.

---

## 16. Stop When the Evidence Changes the Task

Pause further modification when you discover that:

- the requested change conflicts with an existing requirement;
- a migration may destroy or reinterpret data;
- an external contract would need to change unexpectedly;
- the repository contains contradictory implementations;
- required credentials or external services are unavailable;
- the apparent bug is caused elsewhere;
- the requested solution would create a significant security risk.

Investigate first. If the correct choice cannot be inferred safely, report the issue rather than guessing.

---

## 17. Definition of Done

Before considering a change complete, check:

- The requested behaviour is implemented.
- The change is no broader than necessary.
- Existing conventions have been followed.
- Relevant interfaces and invariants remain valid.
- Input and permission boundaries are enforced.
- Persistent data is handled safely.
- Relevant tests have been added or updated.
- Relevant tests have been run.
- Errors and logs are appropriate.
- No secrets or debugging artefacts were introduced.
- Dependencies were changed only when necessary.
- Documentation reflects user-visible or operational changes.
- The final diff has been reviewed as a whole.

When several solutions are valid, prefer the one that future maintainers will understand most quickly.