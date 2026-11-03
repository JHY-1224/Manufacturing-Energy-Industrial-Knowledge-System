import json
from pathlib import Path

from .base_loader import BaseLoader, LoadedDocument, decode_text


class ChatMLLoader(BaseLoader):
    def load(self, path: str | Path) -> list[LoadedDocument]:
        source = Path(path)
        documents: list[LoadedDocument] = []
        seen_ids = set()
        for index, line in enumerate(decode_text(source).splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid ChatML JSON on line {index}") from exc
            if isinstance(payload, list):
                messages = payload
                attributes = {}
            elif isinstance(payload, dict):
                messages = payload.get("messages")
                attributes = payload
            else:
                raise ValueError(f"ChatML line {index} must be an object with messages or a message list")
            if not isinstance(messages, list) or not messages:
                raise ValueError(f"ChatML line {index} requires non-empty messages")
            lines = []
            for message in messages:
                if not isinstance(message, dict) or not isinstance(message.get("role"), str) or message["role"] not in {"system", "user", "assistant", "tool", "function"}:
                    raise ValueError(f"Invalid ChatML message role on line {index}")
                content = message.get("content")
                if not isinstance(content, str) or not content.strip():
                    raise ValueError(f"ChatML line {index} requires non-empty text message content; multimodal/tool-call-only messages are not supported")
                lines.append(f"{message['role']}: {content}")
            case_id = attributes.get("case_id", f"case_{index:04d}")
            if not isinstance(case_id, str) or not case_id.strip() or case_id in seen_ids:
                raise ValueError(f"ChatML line {index} has an invalid or duplicate case_id")
            seen_ids.add(case_id)
            metadata = {
                "format": "chatml", "category": "Case", "case_id": case_id,
                "intent": attributes.get("intent", ""), "node": attributes.get("node", ""),
                "message_count": len(messages), "source_line": index,
            }
            documents.append(LoadedDocument(content="\n".join(lines), source=source.name, metadata=metadata))
        if not documents:
            raise ValueError("ChatML contains no conversation cases")
        return documents
