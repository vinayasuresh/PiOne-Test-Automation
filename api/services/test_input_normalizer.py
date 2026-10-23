"""TestInputNormalizer: Normalizes requirements and test cases into standard structures.

Preserves source references (file, line number) and marks parsing uncertainty
when unstructured text does not strictly match standard schemas.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class NormalizedInputItem:
    item_id: str
    title: str
    description: str
    item_type: str  # "requirement" | "testcase" | "scenario_prompt"
    source_reference: str  # filename or "user_prompt"
    source_line: int | None = None
    uncertainty: bool = False
    tags: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.tags is None:
            self.tags = []

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TestInputNormalizer:
    """Parses text, markdown, or JSON requirements/testcases into standard records."""

    @staticmethod
    def normalize_json(content: str, source_name: str = "upload.json") -> list[NormalizedInputItem]:
        items: list[NormalizedInputItem] = []
        try:
            data = json.loads(content)
            if isinstance(data, list):
                raw_list = data
            elif isinstance(data, dict):
                raw_list = data.get("requirements") or data.get("testcases") or data.get("items") or [data]
            else:
                raw_list = []

            for idx, entry in enumerate(raw_list, start=1):
                if not isinstance(entry, dict):
                    continue
                item_id = str(entry.get("id") or entry.get("tc_id") or entry.get("req_id") or f"REQ_{idx:03d}")
                title = str(entry.get("title") or entry.get("name") or entry.get("summary") or item_id)
                desc = str(entry.get("description") or entry.get("details") or entry.get("text") or title)
                item_type = "testcase" if ("tc_" in item_id.lower() or "test" in item_id.lower()) else "requirement"

                items.append(NormalizedInputItem(
                    item_id=item_id,
                    title=title,
                    description=desc,
                    item_type=item_type,
                    source_reference=source_name,
                    source_line=None,
                    uncertainty=False,
                    tags=entry.get("tags") or [],
                ))
        except Exception:
            # Fall back to text parsing if JSON is malformed
            return TestInputNormalizer.normalize_text(content, source_name)
        return items

    @staticmethod
    def normalize_text(content: str, source_name: str = "upload.txt") -> list[NormalizedInputItem]:
        items: list[NormalizedInputItem] = []
        lines = content.splitlines()

        # Excel/CSV uploads are decoded to TSV. Preserve the user's testcase
        # columns instead of treating every cell as an unrelated paragraph.
        for header_index, raw_header in enumerate(lines[:20]):
            delimiter = "\t" if "\t" in raw_header else ("," if "," in raw_header else "")
            if not delimiter:
                continue
            headers = [h.strip().casefold() for h in raw_header.split(delimiter)]
            if not any("testcase id" in h or h in {"tc id", "tc_id"} for h in headers):
                continue
            for row_index, raw_row in enumerate(lines[header_index + 1:], start=1):
                values = raw_row.split(delimiter)
                if not any(v.strip() for v in values):
                    continue
                record = {headers[i]: values[i].strip() for i in range(min(len(headers), len(values)))}
                item_id = record.get("testcase id") or record.get("tc id") or record.get("tc_id") or f"TC_{row_index:03d}"
                title = record.get("testcase") or record.get("title") or record.get("feature") or item_id
                desc = " | ".join(v for v in (
                    record.get("screen", ""), record.get("feature", ""), record.get("testcase", ""),
                    record.get("steps to execute", ""), record.get("expected result", ""),
                ) if v)
                items.append(NormalizedInputItem(
                    item_id=item_id, title=title, description=desc or title,
                    item_type="testcase", source_reference=source_name,
                    source_line=header_index + row_index + 1, uncertainty=False,
                ))
            return items
        
        current_id = ""
        current_title = ""
        current_lines: list[str] = []
        start_line = 1
        item_counter = 1

        def flush_current():
            nonlocal current_id, current_title, current_lines, start_line, item_counter
            if current_title or current_lines:
                cid = current_id or f"ITEM_{item_counter:03d}"
                title = current_title or (current_lines[0][:80] if current_lines else cid)
                desc = "\n".join(current_lines) if current_lines else title
                item_type = "testcase" if ("tc" in cid.lower() or "test" in cid.lower() or "testcase" in source_name.lower()) else "requirement"
                items.append(NormalizedInputItem(
                    item_id=cid,
                    title=title,
                    description=desc,
                    item_type=item_type,
                    source_reference=source_name,
                    source_line=start_line,
                    uncertainty=not bool(current_id),
                ))
                item_counter += 1
            current_id = ""
            current_title = ""
            current_lines = []

        header_regex = re.compile(r"^(?:#+|\d+[\.\)]|\*|-)?\s*([A-Z]+[-_]\d+|REQ-\d+|TC-\d+)[:\s]*(.*)$", re.IGNORECASE)

        for line_num, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped:
                continue

            match = header_regex.match(stripped)
            if match:
                flush_current()
                current_id = match.group(1).upper()
                current_title = match.group(2).strip() or current_id
                start_line = line_num
            else:
                if not current_lines and not current_title:
                    start_line = line_num
                    current_title = stripped[:80]
                current_lines.append(stripped)

        flush_current()
        return items

    @staticmethod
    def normalize_all(
        requirements_text: str = "",
        testcases_text: str = "",
        user_prompt: str = "",
    ) -> list[NormalizedInputItem]:
        all_items: list[NormalizedInputItem] = []

        if requirements_text.strip():
            txt = requirements_text.strip()
            if txt.startswith("{") or txt.startswith("["):
                all_items.extend(TestInputNormalizer.normalize_json(txt, "requirements.json"))
            else:
                all_items.extend(TestInputNormalizer.normalize_text(txt, "requirements.txt"))

        if testcases_text.strip():
            txt = testcases_text.strip()
            if txt.startswith("{") or txt.startswith("["):
                all_items.extend(TestInputNormalizer.normalize_json(txt, "testcases.json"))
            else:
                all_items.extend(TestInputNormalizer.normalize_text(txt, "testcases.txt"))

        if user_prompt.strip():
            all_items.append(NormalizedInputItem(
                item_id="PROMPT_001",
                title="User Scenario Prompt",
                description=user_prompt.strip(),
                item_type="scenario_prompt",
                source_reference="user_prompt",
                uncertainty=False,
            ))

        return all_items
