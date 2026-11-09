# OmniTest live UI audit — 9 November 2026

## Scope and verdict

**Full application acceptance is not proven.** This is a focused live browser audit of an existing CAT Sentinel run and a separate public read-only API test. Other modules and lifecycle paths remain untested in this audit; the master tracker records the boundaries.

## CAT Sentinel workflow

- OmniTest: `http://127.0.0.1:8000/`; UI reported PostgreSQL.
- Target: `https://catsentinel.rpiinfo.com`; run `1b2c9727a1cc4aab9130d8b63ab24e87`, session **CAT final health retest**.
- The previously created live run was reopened: one Live Events route and 50 elements. This audit did not repeat the complete discovery/crawl.
- Requested two grounded cases for search and the event table; one case was generated, with a shortfall indicated.
- Execution initially reported expired authentication. Refreshing the authorized session through the UI and restarting the test succeeded.
- One case executed two passing **steps**: navigate to Live Events and type into the Search alerts field. There was no assertion that the table changed or filtering was correct. Therefore the requested business behavior is not proven.
- The report and Saved Runs listed the execution. The artifact repository showed the CAT run and its download links.

## Findings

1. **Screenshot viewer — fixed and retested for this run:** Windows path backslashes were interpreted by the inline JavaScript handler. Encoding the path before insertion fixed the issue; after reloading, the CAT step screenshot rendered in the modal. Other paths and missing evidence remain to be tested.
2. **Restored run state and module selection — fixed for this run:** the UI did not treat `executed` as terminal. It also checked all discovered modules during restoration, overwriting the selected IDs in browser state. After correction, Manifest opened the report and the browser showed one selected Live Events module out of 20 discovered, matching the saved run. Other run states remain to be tested.
3. **Coverage shortfall — mitigated, not fully fixed:** the deterministic planner only generates a type action for search inputs. It lacks a grounded results selector and expected data. It now reports a `search_result_unverified` coverage gap and UI warning; it still does not verify table filtering. Two focused planner tests passed. A real assertion needs an explicit expected result and grounded selector.
4. **Target-side console errors:** the execution workbook recorded JavaScript errors from CAT Sentinel's `main` bundle, including a null `tenantName` access and Mapbox style-not-loaded error. These originate in the target bundle as observed; their underlying causes and business impact require CAT Sentinel investigation.

## Export verification

The report UI's Excel, execution Excel, execution JSON, and checkpoint actions each displayed a download-started message. The execution Excel endpoint was independently inspected: HTTP 200, spreadsheet MIME type, and `Content-Disposition: attachment; filename="CAT_final_health_retest_execution_results.xlsx"`. The workbook had one `test_execution` sheet, 22 distinct headers, and two rows corresponding to the two executed steps. Other downloaded contents and filenames were not independently inspected in this audit.

## Separate API test

In the same work session, the rendered API-testing UI imported a one-operation OpenAPI document and executed a public read-only JSONPlaceholder `GET /todos/1`, obtaining HTTP 200 and a contract-satisfied outcome. Its downloaded workbook had ten sheets. This is a public API example, not a CAT Sentinel API test. Schema validation was skipped because the sample did not contain a response schema.

## Still to test

Interactive learning across modules; CAT API tests; all exports and session-name edge cases; baseline creation and replay; saved-run restoration and checkpoint recovery; security modes; dashboard/API/database cross-read; assurance desk lifecycle; all remaining CAT modules; full release gate. No release certification is claimed.

## Follow-up controlled checks

With a separate SQLite test database and fallback explicitly enabled only in the test process, interactive-session service, assurance persistence, and approved database-read tests produced **23 passed**. With local socket access, API-contract, dashboard-data, and security-review suites produced **44 passed, 1 deselected**. These are controlled service tests, not live CAT Sentinel or full rendered-browser evidence. The first socket-restricted run timed out in its test-client runtime; it is not counted as a pass.
