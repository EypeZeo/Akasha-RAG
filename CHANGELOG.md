# Changelog

All notable changes to the **Akasha-RAG** project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

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
