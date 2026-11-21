from collections.abc import Sequence

from backend.app.domain.value_objects.evidence import EvidenceReference


class ContextBuilder:
    @staticmethod
    def build(sources: Sequence[EvidenceReference]) -> str:
        sections: list[str] = []
        for index, source in enumerate(sources, start=1):
            if source.source_file is None or source.start_line is None or source.end_line is None:
                raise ValueError("Retrieved source is missing file or line attribution")
            sections.append(
                f"[Source {index}: {source.source_file}, lines "
                f"{source.start_line}-{source.end_line}]\n{source.excerpt}"
            )
        return "\n\n".join(sections)
