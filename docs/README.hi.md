<p align="right"><a href="../README.md">简体中文</a> · <a href="README.en.md">English</a> · <a href="README.ja.md">日本語</a> · <a href="README.fr.md">Français</a> · <a href="README.de.md">Deutsch</a> · <a href="README.ko.md">한국어</a> · <a href="README.ru.md">Русский</a> · <b>हिन्दी</b></p>

# Akasha-RAG · मल्टी-प्लेटफ़ॉर्म पसंदीदा RAG ज्ञानकोश

[![CI](https://github.com/EypeZeo/Akasha-RAG/actions/workflows/ci.yml/badge.svg)](https://github.com/EypeZeo/Akasha-RAG/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/EypeZeo/Akasha-RAG)](https://github.com/EypeZeo/Akasha-RAG/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](../LICENSE)

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?logo=typescript&logoColor=white)
![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)
![Vite](https://img.shields.io/badge/Vite-6-646CFF?logo=vite&logoColor=white)
![Tailwind CSS](https://img.shields.io/badge/Tailwind_CSS-3-06B6D4?logo=tailwindcss&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)
![ChromaDB](https://img.shields.io/badge/ChromaDB-FF6B6B?logo=chromatic&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-2EAD33?logo=playwright&logoColor=white)

अपने Douyin (चीन का TikTok) और Bilibili पसंदीदा को एक ही खोजने-योग्य, बातचीत-योग्य निजी नॉलेज बेस में बदलें।

```mermaid
flowchart LR
    subgraph संग्रह
        DY[Douyin पसंदीदा]
        BILI[Bilibili पसंदीदा]
    end
    DY --> DL[ऑडियो डाउनलोड<br/>yt-dlp]
    BILI --> DL
    DY --> IMG[इमेज नोट<br/>Qwen-VL विज़न]
    DL --> ASR[ट्रांसक्रिप्शन<br/>DashScope ASR]
    ASR --> CHUNK[चंकिंग]
    IMG --> CHUNK
    CHUNK --> EMBED[एम्बेडिंग]
    EMBED --> VDB[(ChromaDB)]
    VDB --> CHAT[RAG चैट]
```

## जल्दी शुरू करें

### आवश्यकताएँ
- Windows 10 या नया (64-बिट, Windows Server 2016 और उसके बाद के संस्करण सहित)

> वन-क्लिक इंस्टॉलर आपके लिए ज़रूरी हर चीज़ अपने आप तैयार कर देता है — Python, Node.js, ffmpeg, बैकएंड एनवायरनमेंट, फ्रंटएंड डिपेंडेंसी और ब्राउज़र कॉम्पोनेंट — और आपके कंप्यूटर की खुद की किसी सेटिंग को नहीं छूता। Windows 7/8/8.1, Linux या macOS पर नीचे दिया गया "मैन्युअल इंस्टॉल" इस्तेमाल करें।
> कुछ खास सेटअप (Windows Server Core, ARM64 आधारित Windows) पर कुछ जानी-पहचानी कम्पैटिबिलिटी समस्याएँ आ सकती हैं — नीचे "मैन्युअल इंस्टॉल" देखें।

### इंस्टॉल

**वन-क्लिक (अनुशंसित, केवल Windows 10+)**

```powershell
# बस start.bat पर डबल-क्लिक करें -- पहली बार चलाने पर ज़रूरी सब कुछ अपने आप डाउनलोड हो जाता है।
# डाउनलोड प्रोग्रेस दिखेगा, बस पूरा होने तक इंतज़ार करें।
```

**मैन्युअल इंस्टॉल (दूसरे ऑपरेटिंग सिस्टम वालों के लिए / कोड में बदलाव करने वाले डेवलपर्स के लिए)**

```powershell
# बैकएंड
cd backend
pip install uv
uv sync --python 3.12
playwright install chromium
# यहाँ आपको ffmpeg खुद इंस्टॉल करके PATH में जोड़ना होगा
# (वन-क्लिक इंस्टॉलर यह चरण अपने आप कर देता है, मैन्युअल इंस्टॉल नहीं करता)

# फ्रंटएंड
cd ../frontend
npm install
```

### कॉन्फ़िगरेशन

पहली बार चलाने पर, अगर `backend/.env` अभी मौजूद नहीं है, तो लॉन्चर इसे `.env.example` से अपने आप बना देता है; इसके बाद सर्विस शुरू होने से पहले आपको दो कुंजियाँ भरनी होंगी। लॉन्चर सिर्फ़ यह बताता है कि कौन-सा वेरिएबल नाम गायब है और फ़ाइल कहाँ है — कुंजी के मान कभी नहीं दिखाता।

```powershell
cd backend
# अपने आप बनी .env खोलकर संपादित करें (या खुद copy .env.example .env चलाएँ), कम से कम यह भरें:
#   DASHSCOPE_API_KEY=आपकी Alibaba Cloud Bailian API कुंजी   (ट्रांसक्रिप्शन + एम्बेडिंग + इमेज पहचान)
#   DEEPSEEK_API_KEY=आपकी DeepSeek API कुंजी                  (चैट)
```

अगर आपका नेटवर्क इन डाउनलोड पतों तक नहीं पहुँच पाता, तो मिरर इस्तेमाल करने के लिए `AKASHA_RUNTIME_MIRROR=https://आपके-मिरर-का-रूट-पता` सेट करें; मिरर सिर्फ़ यह बदलता है कि इंस्टॉलर फ़ाइलें खुद कहाँ से आती हैं — उन्हें जाँचने वाला चेकसम हमेशा आधिकारिक स्रोत से ही आता है, इसलिए मिरर बदलने से यह सुरक्षा जाँच कभी नहीं छूटती।

### चलाएँ

```powershell
# एक कमांड (बैकएंड + फ्रंटएंड एक ही टर्मिनल में साथ चलते हैं, लॉग अपने आप सहेजे जाते हैं, तैयार होते ही ब्राउज़र खुल जाता है)
start.bat
```

या अलग-अलग चलाएँ: `backend` में `uv run uvicorn app.main:app --reload --port 8000`;
`frontend` में `npm run dev`; फिर http://localhost:5173 खोलें।

> **UI भाषा**: लॉन्चर विंडो का टेक्स्ट पर्यावरण चर `AKASHA_LANG` से बदला जा सकता है —
> `zh` / `en` / `ja` / `fr` / `de` / `ko` / `ru` / `hi` में से एक। सेट न होने पर यह आपके सिस्टम से
> अपने आप पहचाना जाता है, न पहचान पाने पर अंग्रेज़ी पर वापस चला जाता है।
> उदाहरण: `set AKASHA_LANG=hi && start.bat`.

## कार्यप्रवाह

1. "QR से लॉगिन" → ब्राउज़र में Douyin या Bilibili का लॉगिन पेज खुलता है → अपने फ़ोन से स्कैन करें (हर प्लेटफ़ॉर्म अलग-अलग लॉगिन होता है)
2. "सिंक" से पसंदीदा लाएँ (दोबारा क्लिक करने पर नए सिरे से स्क्रैप होता है और जोड़े/हटाए गए की संख्या दिखती है)
3. "इनजेस्ट" → बैकएंड ऑडियो डाउनलोड करता है या इमेज टेक्स्ट निकालता है → ट्रांसक्राइब → एम्बेड (लाइव प्रगति)
4. चैट पैनल में प्रश्न पूछें; "एक्सपोर्ट" इनजेस्ट की गई सामग्री से Word/Excel/Markdown/PPT/PDF बनाता है

> **रीसेट और सफ़ाई दोनों UI से होते हैं** — बाईं ओर "नॉलेज बेस स्थिति" क्षेत्र:
> — **"इनजेस्ट साफ़ करें"**: वेक्टर इंडेक्स रीसेट करता है, ट्रांसक्रिप्शन कैश साफ़ करता है और सब कुछ लंबित पर वापस ले जाता है
> (Embedding मॉडल बदलने के बाद पुनर्निर्माण के लिए);
> — **"🔄 विफल/अटके आइटम को लंबित पर रीसेट करें"**: केवल विफल या अटकी प्रविष्टियों को वापस लाता है, बाक़ी को नहीं छूता।
> (क्रमशः `POST /api/knowledge/clear-all` / `/reset-failed` से मेल खाता है; सामान्यतः मैन्युअल कॉल की ज़रूरत नहीं।)

## तकनीकी स्टैक

| परत | तकनीक |
|-----|-------|
| बैकएंड | FastAPI + SQLAlchemy + SQLite (WAL) + loguru |
| वेक्टर स्टोर | ChromaDB (cosine, 1024 डाइमेंशन) |
| LLM | DeepSeek (OpenAI-संगत) |
| Embedding | DashScope `qwen3.7-text-embedding` (`dimension=1024` स्थिर) |
| ASR | DashScope `paraformer-v2` (रीयल-टाइम पहचान API) |
| विज़न | DashScope `qwen3.7-flash` (इमेज नोट OCR / चार्ट निष्कर्षण) |
| ऑडियो डाउनलोड | yt-dlp + ffmpeg (yt-dlp detail API विफल होने पर ब्राउज़र फ़ॉलबैक) |
| संग्रह / लॉगिन | Playwright + Chromium |
| एक्सपोर्ट | python-docx / openpyxl / python-pptx / reportlab |
| फ्रंटएंड | React 19 + Vite 6 + TypeScript + Tailwind CSS 3 |

मॉडल `.env` (`ASR_MODEL` / `EMBEDDING_MODEL` / `VISION_MODEL`) से बदले जा सकते हैं।
`EMBEDDING_MODEL` बदलने के बाद फ्रंटएंड के "इनजेस्ट साफ़ करें" बटन (या `POST /api/knowledge/clear-all`) से
पुनर्निर्माण करें — वरना पुराने और नए वेक्टर असंगत सिमेंटिक स्पेस में रहेंगे और खोज परिणाम खराब हो जाएँगे।

## प्रोजेक्ट संरचना

```
backend/
├─ app/
│  ├─ main.py                     FastAPI प्रवेश-बिंदु / lifespan
│  ├─ core/config.py              Pydantic Settings
│  ├─ core/logging.py             loguru + uvicorn एक्सेस-लॉग फ़िल्टर
│  ├─ api/routes/                 auth / favorites / knowledge / chat / system
│  └─ services/
│     ├─ douyin_collector.py      Playwright लॉगिन + पसंदीदा स्क्रैप (Douyin)
│     ├─ bilibili/                Bilibili लॉगिन + पसंदीदा स्क्रैप
│     ├─ douyin_media_resolver.py ब्राउज़र फ़ॉलबैक मीडिया रिज़ॉल्यूशन
│     ├─ media_service.py         yt-dlp डाउनलोड + ffmpeg ट्रांसकोड + कैश सफ़ाई
│     ├─ asr_service.py / asr_worker.py   सबप्रोसेस-पृथक DashScope ASR
│     ├─ vision_service.py        Qwen-VL इमेज निष्कर्षण
│     ├─ text_processing.py       सफ़ाई / चंकिंग / शीर्षक हैशटैग हटाना
│     ├─ chroma_service.py        वेक्टर स्टोर I/O (हर प्लेटफ़ॉर्म की अपनी कलेक्शन)
│     ├─ llm_service.py           DeepSeek चैट + DashScope एम्बेडिंग
│     ├─ rag_service.py           पुनर्प्राप्ति + जनरेशन
│     ├─ knowledge_service.py     इनजेस्ट पाइपलाइन ऑर्केस्ट्रेशन
│     ├─ worker.py                इनजेस्ट बैकग्राउंड क्यू
│     ├─ export_worker.py         बैच-एक्सपोर्ट बैकग्राउंड टास्क
│     └─ batch_export_service.py / markdown_export.py   मल्टी-फ़ॉर्मैट एक्सपोर्ट
├─ tests/
└─ pyproject.toml
frontend/
└─ src/
   ├─ App.tsx  api.ts
   ├─ pages/         LandingPage · Workspace
   └─ components/    LoginModal · SourcesPanel · ChatPanel · ExportModal · ...
docs/                              multilingual documentation (README.{en,ja,fr,de,ko,ru,hi}.md)
launcher.py                        बैकएंड + फ्रंटएंड एकीकृत लॉन्चर
launcher_i18n.py                   लॉन्चर बहुभाषी स्ट्रिंग्स
start.bat                          Windows वन-क्लिक स्टार्ट
version.txt                        संस्करण का एकल स्रोत (release-please द्वारा अनुरक्षित)
```

## मुख्य API

| मेथड | पथ | उद्देश्य |
|------|-----|---------|
| POST | `/api/auth/douyin/login/start` · `/status` · `/logout` | Douyin QR लॉगिन |
| POST | `/api/auth/bilibili/login/start` · `/status` · `/logout` | Bilibili QR लॉगिन |
| POST | `/api/favorites/sync` · GET `/collections` · `/collections/{id}/videos` | पसंदीदा |
| POST | `/api/knowledge/sync` · GET `/sync/{task_id}` | इनजेस्ट + प्रगति |
| GET  | `/api/knowledge/stats` | नॉलेज बेस आँकड़े |
| POST | `/api/knowledge/clear-all` · `/reset-failed` | साफ़ / रीसेट |
| POST | `/api/knowledge/export/batch` · GET `/export/batch/{id}` · `/export/batch/{id}/download` | बैच एक्सपोर्ट (बैकग्राउंड टास्क) |
| POST | `/api/system/pick-directory` | नेटिव फ़ोल्डर चयन |
| POST | `/api/chat/ask` · `/ask/stream` · GET `/sessions` · `/sessions/{id}/messages` | चैट |

## लागत

| सेवा | बिलिंग | टिप्पणी |
|------|--------|---------|
| DeepSeek LLM | प्रति टोकन | चैट + एक्सपोर्ट AI सारांश; सस्ता |
| DashScope ASR | प्रति मिनट | मुफ़्त सीमा उपलब्ध |
| DashScope Embedding / Vision | प्रति टोकन | मुफ़्त सीमा उपलब्ध |

## योगदान

कमिट संदेशों में [Conventional Commits](https://www.conventionalcommits.org/) उपसर्ग
(`feat:` / `fix:` / `chore:` …) का उपयोग करें; release-please इनसे संस्करण और Release बनाता है।
सभी बदलाव PR के ज़रिए `main` में आते हैं और CI (बैकएंड pytest + फ्रंटएंड build) पास करने चाहिए।
बग रिपोर्ट के लिए Issue टेम्पलेट का उपयोग करें और त्रुटि स्क्रीनशॉट, `logs/` आउटपुट और अपने पर्यावरण संस्करण संलग्न करें।

## लाइसेंस

[MIT](../LICENSE)
