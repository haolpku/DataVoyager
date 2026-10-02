# Manifest and reporting contract

Use a JSON manifest shaped like this before any multi-source download:

```json
{
  "sources": [
    {
      "dataset_id": "owner/dataset",
      "revision": null,
      "config": "en",
      "split": "train",
      "field_mapping": {"instruction": "question", "output": "answer"},
      "row_budget": 1000
    }
  ],
  "target": {"unit": "retained_records", "count": 1000},
  "cleaning_rules": ["drop_empty_instruction_or_output", "exact_deduplicate"]
}
```

For every stage report:

- state the requested unit and count, plus whether it was reached;
- list each source's config, split, rows read, rows retained, duplicates,
  rejection reasons, and error (if any);
- link the raw, merged, cleaned, manifest, and report artifacts;
- distinguish download/configuration availability from content quality;
- include external model provider usage only when returned by that provider.

When a source cannot be read, include its dataset ID, config, split, endpoint,
HTTP or exception detail, and whether another selected source completed. Do not
collapse mixed success into “all downloads failed.”
