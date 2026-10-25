# GitHub repository tasks

[Home](../README.md) · [Architecture](architecture.md)

This is a local assistant workflow, not GitHub's hosted Copilot service. Git and an appropriate GitHub login are required for authenticated operations; the model remains on local Ollama.

Example task, replacing the repository and issue with your actual target:

> Inspect `https://github.com/owner/repo`, read its instructions, fix the specified issue in an independent branch, run the relevant tests, and prepare a PR for review.

| Tool | Evidence / boundary |
| --- | --- |
| `github_prepare(repository, path)` | Clones to a new workspace-relative directory; creates a `guarded/` branch; never overwrites an existing folder |
| `github_status(path)` | Reads the actual origin, branch, changed files, snapshot, and publication status |
| `github_run_tests(path, command)` | Runs the real command in that repo; records exit status and the current file snapshot |
| `github_publish(path, title, body, files)` | Shows the exact file list/diff for host approval, commits/pushes the independent branch, creates a PR, and verifies the remote commit |

Use normal file tools with workspace-relative paths such as `repo/src/main.py`. Read existing files and instructions before editing. Request real project tests, inspect their output, fix failures, then validate the final file state.

Publication requires a successful host-recorded test on the same file snapshot. It does not prove the chosen test command is sufficient for every task. The user still reviews the command and actual diff. Host code obtains GitHub credentials through the existing Git credential helper; credentials are not returned to the model.

No force push, branch deletion, main-branch publication, or automatic merge is exposed by these tools. An uncertain publication is recorded and not replayed. Automatically appending further changes to an existing PR is not supported by this version; use a new task branch. Repository text and old chat history do not authorize unrelated actions.

Empty repositories can be cloned, inspected (`head: null`, `empty_repository: true`), and tested. Creating a PR requires an existing default-branch commit; the publication tool stops before writing anything when that base does not yet exist. It does not silently initialize or publish a repository's first commit.
