# Devin GitHub Actions Workflow Setup
1. Accept Devin trial account invitation (will be sent to your email)
2. Create DEVIN_API_KEY (this is under Service Users -> Provision service user)
            DEVIN_ORG_ID (this is under Service Users)
          DEVIN_PLAYBOOK_ID (Playbooks -> Create team playbook, use parent-playbook.md)
          DEVIN_CHILD_PLAYBOOK_ID (Playbooks -> Create team playbook, use child-playbook.md)
3. Add them to your GitHub repo with CLI `gh secret set <key>` or manually on GitHub
4. Add `codeql-devin-remediation.yml` to the GitHub actions folder: `.github/workflows/`
5. Run the workflow!