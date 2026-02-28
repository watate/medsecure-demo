"""Dynamic token counting for cost estimation.

Builds the actual prompt that would be sent to an LLM for each alert,
then counts tokens using tiktoken (cl100k_base encoding, which is a
reasonable approximation for all major model families).
"""

import logging

import tiktoken

logger = logging.getLogger(__name__)

# cl100k_base is used by GPT-4/Claude/Gemini-class models as a reasonable approx
_encoding = tiktoken.get_encoding("cl100k_base")

# The prompt template mirrors what the remediation flow would actually send
_PROMPT_TEMPLATE = """You are a security engineer. Fix the following vulnerability in the source code.

## Alert Details
- Rule: {rule_id}
- Severity: {severity}
- Description: {rule_description}
- Message: {message}
- File: {file_path}
- Lines: {start_line}-{end_line}

## Source File ({file_path})
```
{file_content}
```

Return ONLY the complete fixed file content. Do not include explanations."""


def count_tokens(text: str) -> int:
    """Count tokens in a string using cl100k_base encoding."""
    return len(_encoding.encode(text))


def build_prompt_for_alert(
    alert_rule_id: str,
    alert_severity: str,
    alert_rule_description: str,
    alert_message: str,
    alert_file_path: str,
    alert_start_line: int,
    alert_end_line: int,
    file_content: str,
) -> str:
    """Build the remediation prompt for a single alert."""
    return _PROMPT_TEMPLATE.format(
        rule_id=alert_rule_id,
        severity=alert_severity,
        rule_description=alert_rule_description,
        message=alert_message,
        file_path=alert_file_path,
        start_line=alert_start_line,
        end_line=alert_end_line,
        file_content=file_content,
    )


_GROUPED_PROMPT_TEMPLATE = """\
You are a security engineer. Fix ALL of the following vulnerabilities in the source file below.

## Alerts to Fix

{alerts_section}

## Source File ({file_path})
```
{file_content}
```

Return ONLY the complete fixed file content that addresses ALL of the above alerts. Do not include explanations."""


def build_grouped_prompt_for_file(
    file_path: str,
    file_content: str,
    alerts: list[dict[str, object]],
) -> str:
    """Build a single remediation prompt for multiple alerts in the same file.

    Each alert dict should have keys: rule_id, severity, rule_description,
    message, start_line, end_line.
    """
    sections: list[str] = []
    for i, a in enumerate(alerts, 1):
        sections.append(
            f"### Alert {i}\n"
            f"- Rule: {a['rule_id']}\n"
            f"- Severity: {a['severity']}\n"
            f"- Description: {a['rule_description']}\n"
            f"- Message: {a['message']}\n"
            f"- Lines: {a['start_line']}-{a['end_line']}"
        )
    return _GROUPED_PROMPT_TEMPLATE.format(
        alerts_section="\n\n".join(sections),
        file_path=file_path,
        file_content=file_content,
    )


def estimate_prompt_tokens_for_alert(
    alert_rule_id: str,
    alert_severity: str,
    alert_rule_description: str,
    alert_message: str,
    alert_file_path: str,
    alert_start_line: int,
    alert_end_line: int,
    file_content: str,
) -> int:
    """Build the prompt and count its tokens."""
    prompt = build_prompt_for_alert(
        alert_rule_id=alert_rule_id,
        alert_severity=alert_severity,
        alert_rule_description=alert_rule_description,
        alert_message=alert_message,
        alert_file_path=alert_file_path,
        alert_start_line=alert_start_line,
        alert_end_line=alert_end_line,
        file_content=file_content,
    )
    return count_tokens(prompt)
