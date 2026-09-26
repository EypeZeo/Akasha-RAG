# Changelog

All notable changes to the **Akasha-RAG** project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.1.0](https://github.com/EypeZeo/Akasha-RAG/compare/v1.0.1...v1.1.0) (2026-09-26)


### Features

* **chat:** export and print the full session history, not just what's loaded ([#39](https://github.com/EypeZeo/Akasha-RAG/issues/39)) ([9831ff2](https://github.com/EypeZeo/Akasha-RAG/commit/9831ff299e10277a2c596b00be3d939639908476)), closes [#29](https://github.com/EypeZeo/Akasha-RAG/issues/29)
* process-wide model-call admission gate for LLM/embedding/vision ([#12](https://github.com/EypeZeo/Akasha-RAG/issues/12)) ([f2c5418](https://github.com/EypeZeo/Akasha-RAG/commit/f2c5418a20bb1e28a50e7fce356e8162ad022c6b))
* **rag:** add answer and citation metrics ([#54](https://github.com/EypeZeo/Akasha-RAG/issues/54)) ([0f648cd](https://github.com/EypeZeo/Akasha-RAG/commit/0f648cd8505b32289b2b02d38218ace6ea3a6fef))
* **rag:** add deterministic evaluation core ([#51](https://github.com/EypeZeo/Akasha-RAG/issues/51)) ([09ee798](https://github.com/EypeZeo/Akasha-RAG/commit/09ee7981f76cf22244199b0b7012098a7a533d17))
* **rag:** add opt-in live retrieval collector ([#62](https://github.com/EypeZeo/Akasha-RAG/issues/62)) ([8bbf3b6](https://github.com/EypeZeo/Akasha-RAG/commit/8bbf3b6929a661987a38c3ca7e6401ac8268c212))
* **rag:** add replay runner with frozen index manifest ([#53](https://github.com/EypeZeo/Akasha-RAG/issues/53)) ([ee71b00](https://github.com/EypeZeo/Akasha-RAG/commit/ee71b009cb368bf2f63b1188cafd14896b2eeaa6))
* **rag:** add sanitized evaluation traces ([#52](https://github.com/EypeZeo/Akasha-RAG/issues/52)) ([ea0f83c](https://github.com/EypeZeo/Akasha-RAG/commit/ea0f83cf9ddac6e1e566fe88b767a8a8bf6a7be5))
* shared Dialog component with nested-dialog support, aria-modal and focus management ([#15](https://github.com/EypeZeo/Akasha-RAG/issues/15)) ([515f0c8](https://github.com/EypeZeo/Akasha-RAG/commit/515f0c8bfbba2635665a3f104dd00c2dcd7f40d7))


### Bug Fixes

* **app:** polish setup UI and adapt provider networking ([#64](https://github.com/EypeZeo/Akasha-RAG/issues/64)) ([32c4fcb](https://github.com/EypeZeo/Akasha-RAG/commit/32c4fcb6e69c8ae5a73b2ade46c85e4df0aace2c))
* Bilibili sync freshness, part reconciliation, and cancellation correctness ([#10](https://github.com/EypeZeo/Akasha-RAG/issues/10)) ([b8bd2d1](https://github.com/EypeZeo/Akasha-RAG/commit/b8bd2d12a438e4a8f2e0d445c486cf6f99deb854))
* **build-flow:** keep the ingest and export polls alive across remounts and re-renders ([#38](https://github.com/EypeZeo/Akasha-RAG/issues/38)) ([7965b5f](https://github.com/EypeZeo/Akasha-RAG/commit/7965b5ffafa904f04f05814b703f04ad6349b576))
* cancel /ask/stream on client disconnect via a thread bridge instead of running to completion ([#13](https://github.com/EypeZeo/Akasha-RAG/issues/13)) ([613e5e3](https://github.com/EypeZeo/Akasha-RAG/commit/613e5e3e27c9aefdf888bb1cd5dfaf738cb88f71))
* **chat:** enforce client key parity across ask endpoints ([#61](https://github.com/EypeZeo/Akasha-RAG/issues/61)) ([7695ec5](https://github.com/EypeZeo/Akasha-RAG/commit/7695ec515cd7990ffb3dc53e8c30a4aad9feed34))
* **chat:** invalidate every in-flight ChatPanel request on any session transition ([#30](https://github.com/EypeZeo/Akasha-RAG/issues/30)) ([c2e3be2](https://github.com/EypeZeo/Akasha-RAG/commit/c2e3be20dcfe2d9097191c45f7bb77ebf593f418))
* **chat:** notice a client disconnect while the SSE bridge queue is full ([#33](https://github.com/EypeZeo/Akasha-RAG/issues/33)) ([e9903ad](https://github.com/EypeZeo/Akasha-RAG/commit/e9903ad9fb125cc0f49d9412227e9848b728b451)), closes [#28](https://github.com/EypeZeo/Akasha-RAG/issues/28)
* **chat:** show effective platform in collection scope ([#48](https://github.com/EypeZeo/Akasha-RAG/issues/48)) ([c0c979a](https://github.com/EypeZeo/Akasha-RAG/commit/c0c979ad5d2d3e69cb81ba9af2360625e6d53638))
* **collections:** resolve collections by (platform, remote id) and reject ambiguous requests ([#37](https://github.com/EypeZeo/Akasha-RAG/issues/37)) ([ce9c8b1](https://github.com/EypeZeo/Akasha-RAG/commit/ce9c8b14f05936fa2c045268457c52c99b39dcf0)), closes [#34](https://github.com/EypeZeo/Akasha-RAG/issues/34)
* correct export author/link fields and PDF content escaping ([#14](https://github.com/EypeZeo/Akasha-RAG/issues/14)) ([da80193](https://github.com/EypeZeo/Akasha-RAG/commit/da8019374b845f374d12a504c1fdeea6178018c2))
* **douyin:** preserve snapshot through provider sync drift ([#65](https://github.com/EypeZeo/Akasha-RAG/issues/65)) ([837adb9](https://github.com/EypeZeo/Akasha-RAG/commit/837adb94bbf9be8d1966a2bb3f71ca1a4f6755f4))
* **modals:** ignore stale and late responses in the ingest-confirm and export modals ([#45](https://github.com/EypeZeo/Akasha-RAG/issues/45)) ([d8e584a](https://github.com/EypeZeo/Akasha-RAG/commit/d8e584aab1f91ee28ea015730e0f3dee8c50a2d2)), closes [#43](https://github.com/EypeZeo/Akasha-RAG/issues/43)
* move maintenance-op blocking IO off the event loop, guard clear-all against active ingestion ([#11](https://github.com/EypeZeo/Akasha-RAG/issues/11)) ([39f048a](https://github.com/EypeZeo/Akasha-RAG/commit/39f048ab31d4823624725e706edf9a434fb3ecdb))
* PR1 — security, data-integrity, and i18n audit fixes ([#2](https://github.com/EypeZeo/Akasha-RAG/issues/2)) ([03407eb](https://github.com/EypeZeo/Akasha-RAG/commit/03407ebd09b14a2ef504243e9fb54be096d01517))
* **security:** harden logs and remote media handling ([#5](https://github.com/EypeZeo/Akasha-RAG/issues/5)) ([907ae69](https://github.com/EypeZeo/Akasha-RAG/commit/907ae69afdff8bca8dc8d118683bc9e5be18ba9f))
* **security:** protect local credentials with DPAPI ([#6](https://github.com/EypeZeo/Akasha-RAG/issues/6)) ([8d48609](https://github.com/EypeZeo/Akasha-RAG/commit/8d4860979ccfbaf23b5d92318c14d926687c60f2))
* **security:** reject non-loopback API clients ([#7](https://github.com/EypeZeo/Akasha-RAG/issues/7)) ([6fa1a2a](https://github.com/EypeZeo/Akasha-RAG/commit/6fa1a2a69676ec59e3170f7e80d6745465a1755a))
* **sources-panel:** newest stats request wins, no follow-ups after unmount, newest ingest click wins ([#46](https://github.com/EypeZeo/Akasha-RAG/issues/46)) ([8de5480](https://github.com/EypeZeo/Akasha-RAG/commit/8de5480d43c660dc8fbb541107613ba62a5cbac4)), closes [#43](https://github.com/EypeZeo/Akasha-RAG/issues/43)
* **sources:** eliminate two intermittent full-suite test failures in SourcesPanel.test.tsx ([#22](https://github.com/EypeZeo/Akasha-RAG/issues/22)) ([a7c9cae](https://github.com/EypeZeo/Akasha-RAG/commit/a7c9cae873359c911e12842609756dd4fcf0d32b))
* **sources:** give the pagination test headroom over Vitest's default timeout ([#23](https://github.com/EypeZeo/Akasha-RAG/issues/23)) ([4a052e9](https://github.com/EypeZeo/Akasha-RAG/commit/4a052e9a0879e2437c8707acd7cbf9539fdd7722))
* **sources:** scope the panel by (platform, remote id) and ignore stale collection and video responses ([#42](https://github.com/EypeZeo/Akasha-RAG/issues/42)) ([3dc4ce7](https://github.com/EypeZeo/Akasha-RAG/commit/3dc4ce739538e5bdfbde1e46d7bb58078ef71c1f))
* **sources:** use userEvent.click() to fix the pagination test's real flake ([#24](https://github.com/EypeZeo/Akasha-RAG/issues/24)) ([432a20d](https://github.com/EypeZeo/Akasha-RAG/commit/432a20d01bd783c3aefd684d35cf93dc45ee3bbf))


### Performance Improvements

* **frontend:** avoid preloading markdown on landing ([#56](https://github.com/EypeZeo/Akasha-RAG/issues/56)) ([1b0ffb9](https://github.com/EypeZeo/Akasha-RAG/commit/1b0ffb97b4857db7cbff65df908ffdf7d190a7cc))
* **frontend:** lazy load login modal ([#8](https://github.com/EypeZeo/Akasha-RAG/issues/8)) ([924485d](https://github.com/EypeZeo/Akasha-RAG/commit/924485d833b80020c2948ddac7cb3f7318e99e1d))

## [1.0.1](https://github.com/EypeZeo/Akasha-RAG/compare/v1.0.0...v1.0.1) (2026-09-12)


### Bug Fixes

* **docs:** fix Mermaid subgraph syntax and complete the v1.0.0 changelog ([0aa5691](https://github.com/EypeZeo/Akasha-RAG/commit/0aa5691ea649a7ab7bdbcf307152508a160aa4a7))

## [1.0.0](https://github.com/EypeZeo/Akasha-RAG/releases/tag/v1.0.0) (2026-09-12)

Initial public release of Akasha-RAG.

### Features

* Multi-platform favorites ingestion: Douyin and Bilibili accounts each log in independently via QR scan, sync favorites, and feed the same RAG pipeline (audio transcription, image-note vision extraction, chunking, embeddings, platform-partitioned ChromaDB collections).
* RAG chat over your own ingested content, with streaming responses and session history.
* Batch export of ingested content to Word / Excel / Markdown / PPT / PDF.
* Windows one-click setup (`start.bat` → `scripts/bootstrap.ps1`): provisions a project-local Python 3.12, Node.js, ffmpeg, backend virtual environment, frontend dependencies, and Playwright Chromium without touching system PATH or the registry. Supports Windows 10+ (including Server 2016+), with explicit, non-silent handling of the two cases it can't fully verify (Windows Server Core, ARM64 emulation).
* `.env` is created automatically from `.env.example` on first run; missing API keys are reported by variable name only, never by value.
* Soft API-key gate: the app starts regardless of key configuration; a modal prompts for the key only when you actually trigger ingestion or send a chat message, instead of blocking startup outright.
* Settings panel for managing API keys locally: a DashScope key field (shared by transcription, embeddings, and vision), plus a saved list of chat providers supporting both OpenAI-compatible and Anthropic-compatible protocols, switchable at any time — all stored only on your machine.
* New Akasha-RAG logo and favicon.

### Documentation

* README rewritten in all 8 languages to reflect the multi-platform (Douyin + Bilibili) architecture, with a Mermaid pipeline diagram replacing the old ASCII sketch and plainer wording throughout the install/config sections.

### 详细说明

这是 Akasha-RAG 的首个公开发布版本。项目此前以私有仓库的形式迭代到 v0.7.5，这次连同一批新功能一起，作为全新公开仓库的第一个版本发布，版本号从 1.0.0 重新开始计数。
