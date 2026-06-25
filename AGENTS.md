# AGENTS.md - Persistent Rules for All AI Coding Agents

## Core Philosophy
- Strictly follow TDD Red-Green-Refactor workflow on every task.
- Agents write code + tests. Human only specifies requirements, runs tests, and verifies behavior locally. Human never reads or edits source code.
- All changes must be driven by tests.
- Keep everything simple, maintainable, and portable.

## TDD Rules (Red-Green-Refactor)
1. Always start with tests:
   - Comprehensive happy path tests
   - Failure modes and error handling tests
   - Edge cases and boundary conditions
2. Run tests — they must fail initially (Red).
3. Write the minimal code necessary to make all tests pass (Green).
4. Refactor for cleanliness while keeping all tests green.
5. Output clear test results and any required setup instructions.

## Testing Requirements
- Use pytest (Python) or equivalent best-practice framework for the language.
- Tests must be in a `tests/` directory or standard location.
- Include both unit and relevant integration tests.
- Tests must cover success cases, error cases, and edge cases.
- No tests = invalid change.

## Security & Secrets
- NEVER hardcode secrets, API keys, passwords, URLs, or sensitive values (user's name, account numbers, account balances, address, email, etc).
- Always use environment variables via `.env` file (loaded with python-dotenv or equivalent).
- Add `.env` to .gitignore.
- Store non-sensitive configuration in code or config files.

## Code Guidelines
- No hardcoding of values that belong in config or .env.
- Prefer explicit, readable code over cleverness.
- Use type hints where applicable.
- Follow PEP 8 / standard style for the language.
- Keep functions small and focused.
- Include docstrings for public functions/modules.

## Git & Project Rules
- All non-sensitive code goes to GitHub.
- Never commit sensitive data.
- Use meaningful commit messages.
- Work on feature branches when appropriate.

## Agent Behavior
- Propose a clear step-by-step plan before any file changes (especially in Plan Mode).
- Use tools to run tests and show output.
- After changes, summarize what was done and remind how to run tests.
- If something is ambiguous, ask clarifying questions before proceeding.
- Respect existing project structure and conventions.

## Output Preferences
- Show test commands clearly.
- Provide diffs or summaries of changes.
- End with verification steps for the human.

Follow these rules on every task without exception.
