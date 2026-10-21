# Task Plan: Reproducible factor-results visualization system

## Goal
A reproducible Matplotlib visualization module that produces traceable English PNG/PDF research figures solely from the frozen formal CSV results, without changing data, methods, or study outputs.

## Next Step
Deliver the verified visualization system and complete output inventory.

## Current Phase
Phase 5

## Phases

### Phase 1: Source audit and chart specification
- [x] Scan every formal CSV for fields, rows, and date ranges
- [x] Inspect existing analysis scripts and result dependencies
- [x] Map requested figures to actual supported metrics and definitions
- [x] Verify the available Python plotting dependencies
- **Status:** complete

### Phase 2: Visualization implementation
- [x] Add only the required plotting dependency
- [x] Create unified plot module with root-relative paths and CLI controls
- [x] Create figures directory hierarchy and traceability index
- [x] Update README documentation
- **Status:** complete

### Phase 3: Figure generation
- [x] Syntax check and run safe plotting preflight
- [x] Generate all supported figures without rerunning research modules
- [x] Inspect PNG/PDF outputs and source-data preservation
- **Status:** complete

### Phase 4: Verification and delivery
- [x] Validate index, chart files, dates, sample separation, and key values
- [x] Deliver output inventory and known diagnostic limitations
- **Status:** complete

### Phase 5: Delivery
- [x] Confirm complete handoff
- **Status:** complete

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Use Matplotlib only | Requested by the user; its absence is the only missing dependency and Seaborn is not required. |
| Produce one content-rich figure per requested identifier | Avoids redundant sample-specific file proliferation while preserving sample separation through facets, line styles, and legends. |
| Generate both PNG and PDF from each plotting call | PNG meets 300-DPI report use; PDF preserves the same vector figure content. |
| Use actual HAC standard errors for V02 | Supports reported normal-approximation intervals without inventing an estimator. |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| Matplotlib is absent from the active Python environment | Install only Matplotlib, then record it in requirements. |
| First validation import did not register the temporary module in `sys.modules` | Re-ran the read-only validator with explicit module registration; plotting system itself was unaffected. |
