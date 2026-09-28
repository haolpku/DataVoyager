# Source and third-party notices

This initial repository imports the user-supplied `dataflowwebagent.tar.gz`.
The archive did not contain a project-level license. This file does not relicense it
or grant rights to bundled third-party material. Project-level licensing remains an
owner decision before a public open-source release.

- DataFlow integration uses the separately installed `open-dataflow` package:
  https://github.com/OpenDCAI/DataFlow
- The web kernel documents inspiration from browser-use:
  https://github.com/browser-use/browser-use
- The bundled `src/dataflowwebagent/agents/Obtainer/datamixer/assets/skill-creator/`
  directory includes its original Apache-2.0 `license.txt`.
- Imported HTML examples preserve their source URLs and license notes in
  `examples/datamixer_l1_l3_pipeline/source_pages/manifest.jsonl`.
- Imported trial inputs, outputs and benchmark samples retain their existing metadata.
  They are recorded examples, not a newly evaluated benchmark or a general redistribution grant.

Dependencies retain their respective licenses. Python and Node dependencies are
installed separately rather than vendored as executable third-party packages.
