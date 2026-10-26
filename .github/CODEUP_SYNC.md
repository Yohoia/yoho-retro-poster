# Codeup synchronization

Push changes to GitHub as usual. The **Sync to Codeup** GitHub Actions workflow
copies every branch and tag to Codeup over SSH and verifies the resulting commit
references. Synchronization is asynchronous; wait for a successful Actions run
before expecting a new commit in Codeup.

GitHub is the source of changes. Branch/tag updates and deletions follow GitHub;
direct edits in Codeup can be replaced by the next synchronization. All tracked
files and their Git history are copied. A developer's working files are not used
by the workflow.

## Shared configuration

These repositories use the same workflow and the same dedicated synchronization
SSH key:

| GitHub repository | Codeup repository |
| --- | --- |
| `Yohoia/PageHush` | `69aa892db28d0aba0f5ed47e/PageHush` |
| `Yohoia/specora` | `69aa892db28d0aba0f5ed47e/specora` |
| `Yohoia/yoho-blog` | `69aa892db28d0aba0f5ed47e/blog` |
| `Yohoia/yoho-standard-readme` | `69aa892db28d0aba0f5ed47e/yoho-standard-readme` |
| `Yohoia/yoho-retro-poster` | `69aa892db28d0aba0f5ed47e/yoho-retro-poster` |
| `Yohoia/yoho-speckit` | `69aa892db28d0aba0f5ed47e/yoho-speckit` |

Each GitHub repository configures:

- Actions secret `CODEUP_SSH_KEY_B64`: the base64-encoded OpenSSH private key.
  Base64 is an encoding, not encryption; GitHub Secrets provides encrypted storage.
- Repository variable `CODEUP_SSH_URL`: the corresponding
  `git@codeup.aliyun.com:<organization>/<repository>.git` address.

GitHub reads use the built-in, read-only `GITHUB_TOKEN`. The workflow pins the
Codeup host key and enables strict host verification. Credentials are never
committed to Git.

## Check, retry, or rotate

Open **Actions > Sync to Codeup**. Every push triggers a synchronization; use
**Run workflow** or rerun a failed job when needed.

The SSH public key is registered in the Codeup account's personal SSH keys as
`github-codeup-sync-20261026`. It does not use an expiring access token. To rotate
it, register a replacement public key, update `CODEUP_SSH_KEY_B64` in all six
repositories, verify successful runs, then remove the old public key in Codeup.

Local Git remotes should push only to GitHub. If an old clone has an additional
Codeup push URL, remove that extra push URL after the Actions workflow is active
to avoid a second, independent synchronization path.
