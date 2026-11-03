# Contributing

English | [日本語](CONTRIBUTING.ja.md)

Thank you for helping improve Morphe AutoBuilds. Bug reports, documentation fixes, tests, and focused code changes are welcome.

## Before you start

- Check the [README](README.md) for user information and [SETUP.md](SETUP.md) for repository behavior and maintainer configuration.
- Search existing [issues](https://github.com/almeki876/Morphe-AutoBuilds/issues) and pull requests before opening a duplicate.
- For a large change or a new feature, open an issue first so the scope can be discussed.
- Do not include APKs, credentials, access tokens, personal data, or other people’s private information in a commit or issue attachment. Redact logs and screenshots before sharing them.

## Report a problem or suggest an improvement

Use **Issues → New issue** and select the form that best matches the topic. English and Japanese reports are both welcome. Include the app, patch source, release or workflow run, expected behavior, actual behavior, and steps to reproduce when relevant.

Attach only small, relevant screenshots or log excerpts. Drag them into the issue description, paste them, or use GitHub’s attachment control. Do not upload APK files; link to the relevant release instead.

## Make a change

1. Fork the repository and create a branch for your change.
2. Keep the change focused and follow existing Python, JSON, YAML, and documentation conventions.
3. Add or update tests for behavior changes. Do not commit generated APKs, secrets, or local build output.
4. Run the checks below before opening a pull request.
5. Open a pull request against `main`. Explain the problem, the change, how it was tested, and any user-visible impact. Link the related issue when available.

## Run checks locally

Use Python 3.11 and install the pinned project dependencies:

```bash
python3 -m pip install -r requirements.txt
python3 scripts/validate_repository.py
python3 -m compileall -q src scripts
python3 -m unittest discover tests
```

For GitHub Actions changes, also run [actionlint](https://github.com/rhysd/actionlint) against `.github/workflows/` and validate any changed issue form YAML. The `Repository Validation` workflow runs the repository configuration checks and unit tests on pull requests.

## Pull request expectations

- Keep changes reviewable; split unrelated changes into separate pull requests.
- Update the relevant English and Japanese documentation when behavior or instructions change.
- Explain any workflow permissions, triggers, secrets, or release behavior that changes.
- Report tests you ran and call out checks you could not run.
- Do not include unrelated formatting changes or generated release artifacts.

## Security concerns

Do not report security vulnerabilities in a public issue. Contact the repository maintainers privately through GitHub.
