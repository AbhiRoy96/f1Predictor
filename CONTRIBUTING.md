# Contributing to F1 Race Intelligence and Prediction

Thank you for your interest in contributing to the F1 Race Intelligence and Prediction project. Collaborative contributions help improve model accuracy, pipeline efficiency, and analytical utility for the community.

## Code of Conduct

Please foster an open, welcoming, and constructive environment. Treat all contributors and users with respect and professionalism.

## Getting Started

1. **Fork and Clone**: Fork the repository on GitHub and clone your fork locally.
2. **Environment Setup**:
   - Ensure Python 3.11+ (Python 3.12 recommended) is installed.
   - Create and activate a virtual environment:
     ```bash
     python -m venv .venv
     # Windows (PowerShell):
     .\.venv\Scripts\Activate.ps1
     # macOS / Linux:
     source .venv/bin/activate
     ```
   - Install dependencies:
     ```bash
     pip install -r requirements.txt pytest
     ```

## Development Guidelines

### 1. Data Integrity and Leakage Prevention
- Strictly respect chronological point-in-time constraints.
- Future or target session outcomes must never be exposed to historical feature sets or training pipelines.
- Stage-based evidence cutoffs (`pre_weekend`, `post_fp`, `post_quali`) must be maintained consistently.

### 2. Code Style and Quality
- Follow standard Python PEP 8 conventions.
- Keep probabilistic and statistical assumptions clear, reproducible (use seeds where appropriate), and well-documented.
- Maintain existing architecture conventions across `src/data`, `src/features`, `src/models`, `src/simulation`, and `app/`.

### 3. Testing and Verification
- Run the test suite before submitting changes:
  ```bash
  python -m unittest discover -s tests
  ```
  or:
  ```bash
  pytest
  ```
- Add unit or integration tests for new features, bug fixes, or modeling enhancements.

## Pull Request Process

1. Create a descriptive feature branch (`feature/your-improvement` or `fix/issue-description`).
2. Make focused, atomic commits with clear messages.
3. Ensure all existing and new tests pass.
4. Submit a Pull Request targeting the `master` branch.
5. Provide a clear summary in your PR description detailing the problem solved, changes made, and validation results.
