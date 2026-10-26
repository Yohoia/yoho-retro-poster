# Codeup synchronization

Push changes to GitHub as usual. The **Sync to Codeup** GitHub Actions workflow
copies all branches and tags to the matching repository under
`https://codeup.aliyun.com/69aa892db28d0aba0f5ed47e/` and checks that the remote
references match. Branch/tag updates and deletions follow GitHub. Keep GitHub as
the source of changes; direct edits in Codeup can be replaced on the next sync.

The first synchronization includes the complete Git history and all tracked
files, including `SKILL.md` and its supporting resources. Temporary clones do not
change a developer's local repository. No credentials are committed to Git.

## Check or retry a synchronization

Open **Actions > Sync to Codeup** in GitHub. Every push triggers the workflow.
Use **Run workflow** for a manual synchronization or retry a failed run. A
successful run verifies all branch and tag references against its GitHub snapshot.
Synchronization is asynchronous; wait for the workflow to finish before expecting
the new commit in Codeup.

## Credentials

- Repository variable `CODEUP_USERNAME`: the Codeup HTTPS clone username.
- Actions secret `CODEUP_TOKEN`: a Yunxiao personal access token with the
  **Codeup / repositories / read-write** permission.
- GitHub access uses the workflow's built-in, read-only `GITHUB_TOKEN`.

The initial Codeup token expires on **2027-01-24**. Before it expires, create a
replacement in Yunxiao **Personal settings > Personal access tokens** and update
`CODEUP_TOKEN` in each of the three Skill repositories under GitHub
**Settings > Secrets and variables > Actions**. Run the workflow to verify the new
token, then revoke the old token. Use a limited validity period and only the
required permissions.
