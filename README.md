# ClauseLens — Contract Risk Flagger

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.40.2-red.svg)](https://streamlit.io/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**ClauseLens** is a local-first, evidence-grounded contract risk analysis tool built with Streamlit. It reviews PDF and DOCX contracts against user-defined policies, detecting payment terms, renewal clauses, liability caps, and jurisdiction issues with verifiable evidence.

> ⚠️ **Disclaimer**: ClauseLens is decision support only and is not legal advice. Always consult qualified legal counsel for contract review.

## Features

- **Multi-format Support**: Analyze PDF and DOCX contracts
- **Four Policy Dimensions**:
  - Payment terms and deadlines
  - Automatic renewal and cancellation notice
  - Liability caps, exclusions, and indemnification
  - Governing law and jurisdiction
- **Evidence Verification**: Every finding includes exact quotations with source locations
- **Independent Verification**: Cross-checks quotes against extracted text
- **Deterministic Rules**: Primary analysis works offline without AI
- **Optional AI Integration**: OpenAI-compatible models for ambiguous interpretation
- **Export Reports**: JSON and PDF reports with full audit trail
- **Privacy-First**: All processing runs locally; no database or cloud storage

## Screenshots

### Upload & Analysis
![Upload](https://via.placeholder.com/800x400?text=Upload+and+Analysis)

### Risk Dashboard
![Dashboard](https://via.placeholder.com/800x400?text=Risk+Dashboard)

### Evidence Explorer
![Evidence](https://via.placeholder.com/800x400?text=Evidence+Explorer)

## Installation

### Prerequisites

- Python 3.12 or higher
- pip package manager

### Setup

1. Clone the repository:
```bash
git clone https://github.com/vikrambtech2025-png/ClauseLens.git
cd ClauseLens
```

2. Create a virtual environment:
```bash
python -m venv .venv
```

3. Activate the virtual environment:

**Windows:**
```bash
.venv\Scripts\activate
```

**Linux/Mac:**
```bash
source .venv/bin/activate
```

4. Install dependencies:
```bash
pip install -r requirements.txt
```

5. (Optional) Configure AI integration:
```bash
cp .env.example .env
# Edit .env with your OpenAI API key
```

## Usage

### Running the Application

```bash
streamlit run app.py
```

The application will open in your browser at `http://localhost:8501`.

### Quick Start

1. **Upload a Contract**: Click "Upload" and select a PDF or DOCX file
2. **Run Demo**: Use the built-in demo contract to test all features
3. **Configure Policy**: Adjust payment limits, renewal rules, liability requirements
4. **View Results**: Check the risk dashboard for findings
5. **Explore Evidence**: Verify quotations and source locations
6. **Export Reports**: Download JSON or PDF reports

## Policy Configuration

### Default Policy

| Setting | Default Value |
|---------|---------------|
| Maximum payment period | 30 calendar days |
| Automatic renewal | Not permitted |
| Minimum renewal notice | 30 days |
| Liability cap | Required |
| Approved jurisdictions | Delaware, New York, California, United States |
| Exclusive jurisdiction | Permitted |

### Custom Policies

Navigate to **Policy settings** to customize:
- Payment deadlines and day basis (calendar/business days)
- Auto-renewal permissions and notice windows
- Liability cap requirements
- Approved jurisdictions
- Preference flags for advisory warnings

## Risk Classification

| Status | Meaning |
|--------|---------|
| `HIGH` | Hard constraint violation with verified evidence |
| `MEDIUM` | Lower-severity confirmed issue |
| `LOW` | Soft-preference flag (advisory) |
| `REVIEW_REQUIRED` | Ambiguity requiring human review |
| `NO_CONFLICT_FOUND` | Clause found and compliant |
| `NOT_FOUND` | No clause detected (not proof of absence) |
| `NOT_VERIFIED` | Source could not be fully processed |

## Project Structure

```
ClauseLens/
├── app.py                      # Streamlit entry point
├── config/
│   └── settings.py             # Environment configuration
├── core/
│   ├── analysis.py             # Analysis orchestration
│   ├── document_loader.py       # PDF/DOCX extraction
│   ├── clause_splitter.py       # Text segmentation
│   ├── retrieval.py            # Clause retrieval (BM25)
│   ├── policy_engine.py        # Deterministic policy rules
│   ├── evidence_verifier.py     # Independent verification
│   ├── risk_classifier.py      # Status classification
│   ├── llm_analyzer.py         # Optional AI integration
│   ├── prompt_safety.py        # Prompt injection detection
│   ├── report_generator.py      # JSON/PDF export
│   └── text_utils.py           # Text utilities
├── models/
│   └── schemas.py              # Pydantic data models
├── ui/
│   ├── upload.py               # Upload page
│   ├── policies.py             # Policy configuration
│   ├── dashboard.py            # Risk dashboard
│   ├── evidence_viewer.py      # Evidence explorer
│   ├── review_export.py        # Review and export
│   └── common.py               # Shared UI helpers
├── tests/
│   ├── test_ingestion.py       # Document extraction tests
│   ├── test_policy_and_verification.py  # Policy/verification tests
│   ├── test_llm.py             # AI integration tests
│   ├── test_reports.py         # Export tests
│   ├── test_ui_app.py          # Streamlit AppTest
│   └── test_end_to_end.py     # End-to-end benchmark
├── samples/                    # Demo contracts
├── scripts/
│   ├── make_samples.py         # Generate demo contracts
│   └── measure.py              # Run benchmark
├── requirements.txt            # Python dependencies
├── .env.example               # Environment template
└── README.md                  # This file
```

## Testing

### Run All Tests

```bash
pytest -q
```

### Run Specific Test Suites

```bash
# Test document extraction
pytest tests/test_ingestion.py

# Test policy engine
pytest tests/test_policy_and_verification.py

# Test end-to-end
pytest tests/test_end_to_end.py
```

### Benchmark

Run the synthetic contract benchmark:

```bash
python scripts/measure.py
```

## Performance

Measured on synthetic contracts (Windows, Python 3.12):

| Metric | Result |
|--------|--------|
| Median latency | 20-30 ms |
| p95 latency | 40-55 ms |
| Test suite | 51 tests pass |
| Evidence verification | 100% (24/24) |
| Label precision | 1.0000 (30/30) |
| Label recall | 1.0000 (30/30) |

## Limitations

- Retrieval is lexical (keywords + BM25), not embedding-based
- OCR is not included (scanned PDFs detected and marked NOT_VERIFIED)
- Business-day periods use conservative conversion without holiday calendars
- DOCX extraction covers body paragraphs and table rows only
- PDF processing capped at 300 pages
- Contract size capped at 10 MB
- Severity is a heuristic, not a legal conclusion

## Privacy & Security

- All processing runs locally on your machine
- No database, server-side persistence, or analytics
- Uploaded files stored in OS temp directory only
- Review decisions stored in session, not in database
- API keys read from `.env` and never written to reports
- Contract text sent externally only when AI interpretation is enabled
- PDF reports never include full contract text

## Configuration

### Environment Variables

Create a `.env` file from `.env.example`:

```bash
# Optional AI interpretation
OPENAI_API_KEY=your_api_key_here
OPENAI_BASE_URL=https://api.openai.com/v1
CLAUSELENS_LLM_PROVIDER=openai
CLAUSELENS_LLM_MODEL=gpt-4o-mini
CLAUSELENS_ALLOW_EXTERNAL_LLM=true

# Processing limits
CLAUSELENS_MAX_UPLOAD_MB=10
CLAUSELENS_MAX_PDF_PAGES=300
CLAUSELENS_TOP_K=8
CLAUSELENS_LLM_TIMEOUT_SECONDS=30
```

### Streamlit Configuration

Edit `.streamlit/config.toml` to customize:
- Upload size limits
- Theme colors
- Browser settings

## Troubleshooting

**Streamlit not recognized:**
```bash
python -m streamlit run app.py
```

**Port already in use:**
```bash
streamlit run app.py --server.port 8517
```

**Import errors:**
```bash
pip install -r requirements.txt --upgrade
```

**AI toggle not working:**
- Check `.env` contains valid `OPENAI_API_KEY`
- Ensure `CLAUSELENS_ALLOW_EXTERNAL_LLM=true`
- Restart Streamlit after changing `.env`

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Write tests for new functionality
4. Ensure all tests pass
5. Submit a pull request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Built with [Streamlit](https://streamlit.io/)
- PDF processing with [PyMuPDF](https://pymupdf.readthedocs.io/)
- DOCX processing with [python-docx](https://python-docx.readthedocs.io/)
- Retrieval with [rank-bm25](https://github.com/dorianbrown/rank_bm25)
- PDF generation with [ReportLab](https://www.reportlab.com/)

## Contact

For questions, issues, or suggestions:
- Open an issue on GitHub
- Email: vikrambtech2025@gmail.com

---

**ClauseLens** — Evidence-grounded contract risk review. Decision support only, not legal advice.
